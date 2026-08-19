"""存活探针只能在"重启才能修"的故障上失败。

compose 的 healthcheck 会在存活探针连续失败后 `kill -TERM 1`，因此这条探针的
判据直接决定容器什么时候被重启。真机踩过的坑是把就绪信号塞了进去：队列打满
（背压）和 Gateway 断线（无限重连中的暂态）都被判成"坏了"，压满的 Worker 自杀，
在途 run 因换代进 DLQ，多节点级联丢 30%~50%。

因此判据是：
  * 忙（队列满）不算故障；
  * 断线重连中不算故障——ReconnectConfig.max_attempts 默认 0（无限重试），
    重启进程既修不好网络，还会丢掉在途任务；
  * 引擎停了、传输被永久 halt（lease 撤销 / 认证中止，transport._running=False）
    才算故障——那两种状态只有重启能恢复。
"""

from types import SimpleNamespace

from antcode_worker.app.wiring import _create_observability_server
from antcode_worker.observability.health import HealthStatus

MAX_CONCURRENT = 4
FULL_QUEUE = MAX_CONCURRENT * 2


def _server(*, queue_size: int = 0, engine_running: bool = True, connected: bool = True, transport_up: bool = True):
    engine = SimpleNamespace(
        get_stats=lambda: {
            "running": engine_running,
            "polling": True,
            "queue_size": queue_size,
            "max_concurrent": MAX_CONCURRENT,
        }
    )
    transport = SimpleNamespace(is_connected=connected, is_running=transport_up)
    server = _create_observability_server(None, transport, engine)
    server.set_ready(True)
    return server


def _liveness(server):
    return server._health_checker.liveness()


def _readiness(server):
    return server._health_checker.readiness()


def test_full_queue_is_alive() -> None:
    """队列打满是背压，存活探针必须放行，否则最忙的节点先被杀。"""
    assert _liveness(_server(queue_size=FULL_QUEUE)).status == HealthStatus.HEALTHY


def test_reconnecting_transport_is_alive() -> None:
    """断线重连是暂态：max_attempts=0 意味着永远重试，重启只会丢在途 run。"""
    result = _liveness(_server(connected=False, transport_up=True))

    assert result.status == HealthStatus.HEALTHY, result.details


def test_stopped_engine_is_not_alive() -> None:
    """引擎停了只有重启能恢复，必须让容器被重启。"""
    result = _liveness(_server(engine_running=False))

    assert result.status == HealthStatus.UNHEALTHY
    assert result.details == {"engine": HealthStatus.UNHEALTHY.value}


def test_permanently_halted_transport_is_not_alive() -> None:
    """lease 被撤销 / 认证中止后 transport 停掉重连并置 _running=False。

    这时进程还活着却再也接不到任务，只有换一个进程重新注册才能恢复——
    fail-closed 不能因为"不再用 readiness 决定重启"而丢掉这条。
    """
    result = _liveness(_server(connected=False, transport_up=False))

    assert result.status == HealthStatus.UNHEALTHY


def test_readiness_still_reports_a_disconnected_transport() -> None:
    """把断线从"重启判据"里摘掉，不等于不再上报：它仍要让容器转 unhealthy。"""
    result = _readiness(_server(connected=False, transport_up=True))

    assert result.status == HealthStatus.UNHEALTHY
    assert result.details["transport"] == HealthStatus.UNHEALTHY.value


def test_readiness_of_a_busy_but_connected_worker_stays_healthy() -> None:
    result = _readiness(_server(queue_size=FULL_QUEUE * 3))

    assert result.status == HealthStatus.HEALTHY, result.details


def test_readiness_only_checks_do_not_leak_into_liveness() -> None:
    """未标记 liveness 的检查不得影响存活探针。

    HealthChecker 曾经把所有注册项都算进 readiness 而 liveness 恒为 healthy；
    现在两边分开，注册方式就是唯一的开关，不能再被"顺手全跑一遍"抹平。
    """
    server = _server()
    server.register_health_check("readiness-only", lambda: _unhealthy())

    assert _liveness(server).status == HealthStatus.HEALTHY
    assert _readiness(server).status == HealthStatus.UNHEALTHY


def _unhealthy():
    from antcode_worker.observability.health import HealthResult

    return HealthResult(status=HealthStatus.UNHEALTHY, message="dependency down")
