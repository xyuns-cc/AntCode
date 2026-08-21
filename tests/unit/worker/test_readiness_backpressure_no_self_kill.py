"""压满的 Worker 不得把自己判成不健康。

多节点压测实测的故障链（真机复现，非推演）：
  队列打满 -> 引擎检查返回 DEGRADED -> readiness() 只认全 HEALTHY -> /health/ready 503
  -> worker compose 的 `curl -fsS /health/ready || { kill -TERM 1; }` 把 PID 1 杀掉
  -> Worker 重启换 lease 代 -> 在途 ready 消息因代际不匹配进 DLQ
  -> 这些 run 永远不上报 RUNNING，180s 后被判 "节点未在 180s 内上报 RUNNING"。

因此判据是：**队列满时 readiness 必须仍然是 HEALTHY**；只有引擎停/传输断才算真故障。
"""

from types import SimpleNamespace

from antcode_worker.app.wiring import _create_observability_server
from antcode_worker.observability.health import HealthStatus

MAX_CONCURRENT = 4
# Scheduler 的容量就是 max_concurrent * 2（engine.py::Engine.__init__），
# 所以"满队列"恒等于这个值，不是一个可以调开的边界。
FULL_QUEUE = MAX_CONCURRENT * 2


def _server(*, queue_size: int, running: bool = True, connected: bool = True):
    engine = SimpleNamespace(
        get_stats=lambda: {
            "running": running,
            "polling": True,
            "queue_size": queue_size,
            "max_concurrent": MAX_CONCURRENT,
        }
    )
    transport = SimpleNamespace(is_connected=connected, is_running=True)
    # 指标采集器只服务 /metrics，本文件只测健康探针，给一个不会被调用的占位。
    server = _create_observability_server(transport, engine, SimpleNamespace())
    # 启动完成后 lifecycle 会置就绪；不置的话 readiness 直接短路成 "not ready"，
    # 就测不到下面的各项检查了。
    server.set_ready(True)
    return server


def _readiness(server):
    return server._health_checker.readiness()


def test_full_queue_stays_ready_so_busy_worker_is_not_killed():
    """队列正好满：这是背压，readiness 必须 200，否则 healthcheck 会自杀。"""
    result = _readiness(_server(queue_size=FULL_QUEUE))

    assert result.status == HealthStatus.HEALTHY, result.details
    assert result.details["engine"] == HealthStatus.HEALTHY.value


def test_queue_beyond_nominal_capacity_stays_ready():
    """即便读到超过标称容量的瞬时值，也不能降级——同样是背压。"""
    result = _readiness(_server(queue_size=FULL_QUEUE * 3))

    assert result.status == HealthStatus.HEALTHY, result.details


def test_stopped_engine_is_unhealthy():
    """引擎停了才是真故障，必须暴露成不健康。"""
    result = _readiness(_server(queue_size=0, running=False))

    assert result.status == HealthStatus.UNHEALTHY
    assert result.details["engine"] == HealthStatus.UNHEALTHY.value


def test_offline_transport_is_unhealthy():
    """传输层断开是真故障，readiness 必须失败。"""
    result = _readiness(_server(queue_size=0, connected=False))

    assert result.status == HealthStatus.UNHEALTHY
    assert result.details["transport"] == HealthStatus.UNHEALTHY.value
