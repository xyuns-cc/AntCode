"""
可观测性验证测试

Checkpoint 20: 验证健康检查端点和 Prometheus 指标暴露

Requirements: 12.1, 12.2
"""

from types import SimpleNamespace

import pytest
from antcode_worker.observability.metrics import MetricsCollector

# 检查 aiohttp 是否可用
try:
    import aiohttp

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytestmark = pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")

_COUNTER_VALUE = 5
_EXTRA_INCREMENT = 3
_SINGLE_INCREMENTS = 2
_GAUGE_VALUE = 3.0


def _metrics_source():
    """/metrics 的三个百分比只能来自心跳采集器。本套件只验端点与格式，给一个空快照即可；
    口径正确性由 tests/unit/worker/test_prometheus_shares_the_heartbeat_metric_source.py 负责。
    """
    from antcode_worker.heartbeat.metric_models import SystemMetrics

    async def collect(use_cache: bool = True) -> SystemMetrics:
        return SystemMetrics()

    return SimpleNamespace(collect=collect)


class TestHealthChecker:
    """测试健康检查器"""

    def test_liveness_returns_healthy(self):
        """存活探针应返回健康状态"""
        from antcode_worker.observability.health import HealthChecker, HealthStatus

        checker = HealthChecker()
        result = checker.liveness()

        assert result.status == HealthStatus.HEALTHY
        assert result.message == "alive"

    def test_readiness_not_ready_by_default(self):
        """就绪探针默认返回未就绪"""
        from antcode_worker.observability.health import HealthChecker, HealthStatus

        checker = HealthChecker()
        result = checker.readiness()

        assert result.status == HealthStatus.UNHEALTHY
        assert result.message == "not ready"

    def test_readiness_ready_after_set(self):
        """设置就绪后应返回就绪状态"""
        from antcode_worker.observability.health import HealthChecker, HealthStatus

        checker = HealthChecker()
        checker.set_ready(True)
        result = checker.readiness()

        assert result.status == HealthStatus.HEALTHY
        assert result.message == "ready"

    def test_register_health_check(self):
        """注册健康检查应被执行"""
        from antcode_worker.observability.health import (
            HealthChecker,
            HealthResult,
            HealthStatus,
        )

        checker = HealthChecker()
        checker.set_ready(True)

        # 注册一个健康检查
        def redis_check():
            return HealthResult(status=HealthStatus.HEALTHY, message="redis ok")

        checker.register("redis", redis_check)
        result = checker.readiness()

        assert result.status == HealthStatus.HEALTHY
        assert "redis" in result.details
        assert result.details["redis"] == "healthy"

    def test_unhealthy_check_degrades_status(self):
        """不健康的检查应降级整体状态"""
        from antcode_worker.observability.health import (
            HealthChecker,
            HealthResult,
            HealthStatus,
        )

        checker = HealthChecker()
        checker.set_ready(True)

        # 注册一个不健康的检查
        def failing_check():
            return HealthResult(status=HealthStatus.UNHEALTHY, message="failed")

        checker.register("failing", failing_check)
        result = checker.readiness()

        assert result.status == HealthStatus.UNHEALTHY
        assert "failing" in result.details


class TestMetricsCollector:
    """测试指标收集器"""

    @pytest.mark.asyncio
    async def test_counter_increment(self):
        """计数器应正确递增"""
        collector = MetricsCollector(_metrics_source())
        for _ in range(_SINGLE_INCREMENTS):
            collector.inc("tasks_completed")
        collector.inc("tasks_completed", _EXTRA_INCREMENT)

        metrics = await collector.get_all()
        assert metrics["tasks_completed"] == _SINGLE_INCREMENTS + _EXTRA_INCREMENT

    @pytest.mark.asyncio
    async def test_gauge_set(self):
        """仪表应正确设置"""
        collector = MetricsCollector(_metrics_source())
        collector.set("queue_depth", _GAUGE_VALUE)

        metrics = await collector.get_all()
        assert metrics["queue_depth"] == _GAUGE_VALUE

    @pytest.mark.asyncio
    async def test_uptime_tracked(self):
        """应跟踪运行时间"""
        collector = MetricsCollector(_metrics_source())
        metrics = await collector.get_all()

        assert "uptime_seconds" in metrics
        assert metrics["uptime_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_prometheus_format(self):
        """应输出 Prometheus 格式"""
        collector = MetricsCollector(_metrics_source())
        collector.inc("tasks_completed", _COUNTER_VALUE)
        collector.set("queue_depth", _GAUGE_VALUE)

        prometheus_text = await collector.to_prometheus()

        assert f"antcode_worker_tasks_completed {_COUNTER_VALUE}" in prometheus_text
        assert f"antcode_worker_queue_depth {_GAUGE_VALUE}" in prometheus_text
        assert "antcode_worker_uptime_seconds" in prometheus_text


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
class TestObservabilityServer:
    """测试可观测性服务器"""

    @pytest.fixture
    def server(self):
        """创建服务器实例"""
        from antcode_worker.observability.server import ObservabilityServer

        return ObservabilityServer(_metrics_source())

    @pytest.mark.asyncio
    async def test_server_start_stop(self, server):
        """服务器应能启动和停止"""
        # 18081 是 e2e Git HTTP 源的固定端口（scripts/release_e2e_environment.py:42、
        # infra/docker/run-gateway-e2e.sh:31）。本套件与 e2e 常在同一台机器上先后跑，
        # 撞上还没拆的 e2e 栈会稳定 EADDRINUSE，所以这里避开它。
        await server.start(host="127.0.0.1", port=18091)

        # 验证服务器已启动
        assert server._runner is not None
        assert server._site is not None

        await server.stop()

        # 验证服务器已停止
        assert server._runner is None
        assert server._site is None

    @pytest.mark.asyncio
    async def test_health_endpoint(self, server):
        """健康检查端点应返回 ok"""
        await server.start(host="127.0.0.1", port=18082)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18082/health") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "ok"
                    assert data["service"] == "antcode-worker"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_liveness_endpoint(self, server):
        """存活探针端点应返回 healthy"""
        await server.start(host="127.0.0.1", port=18083)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18083/health/live") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "healthy"
                    assert data["message"] == "alive"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_readiness_endpoint_not_ready(self, server):
        """就绪探针端点默认应返回 503"""
        await server.start(host="127.0.0.1", port=18084)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18084/health/ready") as resp:
                    assert resp.status == 503
                    data = await resp.json()
                    assert data["status"] == "unhealthy"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_readiness_endpoint_ready(self, server):
        """设置就绪后就绪探针应返回 200"""
        server.set_ready(True)
        await server.start(host="127.0.0.1", port=18085)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18085/health/ready") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "healthy"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, server):
        """Prometheus 指标端点应返回指标"""
        # 添加一些指标
        server.metrics_collector.inc("test_counter", _COUNTER_VALUE)
        server.metrics_collector.set("test_gauge", _GAUGE_VALUE)

        await server.start(host="127.0.0.1", port=18086)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18086/metrics") as resp:
                    assert resp.status == 200
                    text = await resp.text()

                    # 验证 Prometheus 格式
                    assert f"antcode_worker_test_counter {_COUNTER_VALUE}" in text
                    assert f"antcode_worker_test_gauge {_GAUGE_VALUE}" in text
                    assert "antcode_worker_uptime_seconds" in text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_register_health_check_via_server(self, server):
        """通过服务器注册健康检查"""
        from antcode_worker.observability.health import HealthResult, HealthStatus

        def custom_check():
            return HealthResult(status=HealthStatus.HEALTHY, message="custom ok")

        server.register_health_check("custom", custom_check)
        server.set_ready(True)

        await server.start(host="127.0.0.1", port=18087)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18087/health/ready") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert "custom" in data["details"]
                    assert data["details"]["custom"] == "healthy"
        finally:
            await server.stop()
