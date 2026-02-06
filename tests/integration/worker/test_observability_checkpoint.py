"""
可观测性验证测试

Checkpoint 20: 验证健康检查端点和 Prometheus 指标暴露

Requirements: 12.1, 12.2
"""

import asyncio
import pytest
from unittest.mock import Mock

# 检查 aiohttp 是否可用
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytestmark = pytest.mark.skipif(
    not HAS_AIOHTTP,
    reason="aiohttp not installed"
)


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

    def test_counter_increment(self):
        """计数器应正确递增"""
        from antcode_worker.observability.metrics import MetricsCollector

        collector = MetricsCollector()
        collector.inc("tasks_completed")
        collector.inc("tasks_completed")
        collector.inc("tasks_completed", 3)

        metrics = collector.get_all()
        assert metrics["tasks_completed"] == 5

    def test_gauge_set(self):
        """仪表应正确设置"""
        from antcode_worker.observability.metrics import MetricsCollector

        collector = MetricsCollector()
        collector.set("queue_depth", 10.5)

        metrics = collector.get_all()
        assert metrics["queue_depth"] == 10.5

    def test_uptime_tracked(self):
        """应跟踪运行时间"""
        from antcode_worker.observability.metrics import MetricsCollector

        collector = MetricsCollector()
        metrics = collector.get_all()

        assert "uptime_seconds" in metrics
        assert metrics["uptime_seconds"] >= 0

    def test_prometheus_format(self):
        """应输出 Prometheus 格式"""
        from antcode_worker.observability.metrics import MetricsCollector

        collector = MetricsCollector()
        collector.inc("tasks_completed", 5)
        collector.set("queue_depth", 3.0)

        prometheus_text = collector.to_prometheus()

        assert "antcode_worker_tasks_completed 5" in prometheus_text
        assert "antcode_worker_queue_depth 3.0" in prometheus_text
        assert "antcode_worker_uptime_seconds" in prometheus_text


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
class TestObservabilityServer:
    """测试可观测性服务器"""

    @pytest.fixture
    def server(self):
        """创建服务器实例"""
        from antcode_worker.observability.server import ObservabilityServer

        return ObservabilityServer()

    @pytest.mark.asyncio
    async def test_server_start_stop(self, server):
        """服务器应能启动和停止"""
        # 使用不常用的端口避免冲突
        await server.start(host="127.0.0.1", port=18081)

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
        server.metrics_collector.inc("test_counter", 10)
        server.metrics_collector.set("test_gauge", 5.5)

        await server.start(host="127.0.0.1", port=18086)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18086/metrics") as resp:
                    assert resp.status == 200
                    text = await resp.text()

                    # 验证 Prometheus 格式
                    assert "antcode_worker_test_counter 10" in text
                    assert "antcode_worker_test_gauge 5.5" in text
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
