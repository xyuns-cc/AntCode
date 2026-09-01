"""Redis 读不到负载指标时，派发必须说得出是"读不到"，而不是"大家都很忙"。

``_fetch_resources`` 从前把 Redis 读取包在一个全 ``except`` 里兜成 ``metrics = {}``。
空 dict 交给 ``normalize_worker_metrics`` 会被缺项补成 cpu=100，而 100 正好 >=
``MAX_CPU_THRESHOLD`` —— 于是"读不到"被静默折算成了"这台最忙"这个具体读数。Redis 抖
一下就是**每一台**都被这样折算，全部落选，派发停摆；那个 Redis 异常连一行日志都没有，
运维在 INFO 级日志里只看得到一句"无符合条件节点"，指向的方向完全是错的。

这里钉四件事：

1. 读不到就抛，不许折算成任何一个负载读数（``test_redis_outage_*``）；
2. 全员读不到时，派发失败的日志必须点名 Redis 这个原因（``test_dispatch_shortage_*``）；
3. **成功臂**：同一轮里还读得到的机器照常被选中，读不到的那台照样报出来
   （``test_healthy_worker_*``）；
4. "读不到"与"确实没上报过"不是一件事：后者是关于这台机器的真实结论，fail-closed
   踢掉是对的，但走的是另一条日志、且**不抛**（``test_never_reported_*``）。

**证伪方式**：把 ``worker_resource_probe._read_heartbeat_hash`` 的 ``raise`` 换回
``return {}``，1/2/3 变红；把 ``_fetch_resources`` 里的
``except WorkerMetricsUnavailableError: raise`` 删掉，2/3 变红。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import antcode_core.infrastructure.redis as redis_module
import pytest
from antcode_core.application.services.workers.worker_dispatcher import WorkerLoadBalancer
from antcode_core.application.services.workers.worker_load_score import PERCENT_FULL
from antcode_core.application.services.workers.worker_resource_probe import (
    WorkerMetricsUnavailableError,
    probe_worker_resources,
)
from loguru import logger

from tests.unit.core.broken_metrics_column_support import (
    IDLE_CPU,
    READY_FILTER,
    capture_logs,
    heartbeat,
    worker,
)

_REDIS_OUTAGE = "Connection refused by redis"


def _worker(worker_id: int, name: str):
    # 空的落库指标，逼着走 Redis 心跳这条路。
    return worker(worker_id, name, {})


class _FakeRedis:
    """按 key 决定读得到还是读不到，用来构造"一台好、一台坏"的成功臂。

    与 ``broken_metrics_column_support`` 那个只读得到的假 Redis 分开：本文件要的正是
    "读失败"这条路径，注入失败是它独有的需求。
    """

    def __init__(self, hashes: dict[str, dict[str, str]], failing: set[str]):
        self.hashes = hashes
        self.failing = failing

    async def hgetall(self, key: str) -> dict[str, str]:
        if any(bad in key for bad in self.failing):
            raise ConnectionError(_REDIS_OUTAGE)
        return self.hashes.get(key, {})


def _install_redis(monkeypatch, *, hashes=None, failing=frozenset()):
    fake = _FakeRedis(hashes or {}, set(failing))
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=fake))
    return fake


# --------------------------------------------------------------------------------------
# 1. 读不到就抛，不许折算成读数
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_outage_raises_instead_of_inventing_a_load_reading(monkeypatch) -> None:
    """Redis 读失败必须抛出去，且带得出是哪台、哪个 key、什么原因。

    "为什么必须抛"的依据（空 dict 经 ``normalize_worker_metrics`` 缺项补 100 之后与
    "这台 CPU 打满了"完全同形、真的会被硬门禁踢掉）由第 4 组那条用例顺带钉住：它走的是
    同一个 ``normalize_worker_metrics({})``，断言的也是同样的 cpu 与可用性。
    """
    monkeypatch.setattr(
        redis_module,
        "get_redis_client",
        AsyncMock(side_effect=ConnectionError(_REDIS_OUTAGE)),
    )

    with pytest.raises(WorkerMetricsUnavailableError) as excinfo:
        await probe_worker_resources(_worker(1, "mn-worker-01"))

    assert excinfo.value.worker_name == "mn-worker-01"
    assert "worker-1" in excinfo.value.heartbeat_key
    assert _REDIS_OUTAGE in str(excinfo.value)


# --------------------------------------------------------------------------------------
# 2. 全员读不到：日志必须点名真正的原因
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_shortage_names_the_metrics_failure_not_just_no_eligible_node(monkeypatch) -> None:
    """三台全读不到时，不许只留一句"无符合条件节点"。"""
    workers = [_worker(1, "mn-worker-01"), _worker(2, "mn-worker-02"), _worker(3, "mn-worker-03")]
    _install_redis(monkeypatch, failing={"worker-1", "worker-2", "worker-3"})
    monkeypatch.setattr(READY_FILTER, AsyncMock(return_value=workers))

    records, sink_id = capture_logs()
    try:
        selected = await WorkerLoadBalancer().select_best_worker(workers=workers)
    finally:
        logger.remove(sink_id)

    assert selected is None, "读不到指标时不许硬派给一台我们一无所知的机器"
    errors = records.text("ERROR")
    assert _REDIS_OUTAGE in errors, "Redis 故障本身必须落到日志里，而不是被吞掉"
    for name in ("mn-worker-01", "mn-worker-02", "mn-worker-03"):
        assert name in records.text(), f"{name} 落选的真实原因没被说出来"
    assert "不是它们忙" in records.text(), "落选原因必须与「这几台很忙」区分开"


# --------------------------------------------------------------------------------------
# 3. 成功臂：好机器照常派，坏的照样报
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_worker_is_still_selected_while_another_is_unreadable(monkeypatch) -> None:
    """一台读不到不该牵连全场：读得到的那台必须照常被选中，且它自己不被报成故障。"""
    healthy, unreadable = _worker(1, "mn-healthy"), _worker(2, "mn-unreadable")
    _install_redis(
        monkeypatch,
        hashes={"{antcode}:heartbeat:worker-1": heartbeat(IDLE_CPU)},
        failing={"worker-2"},
    )
    monkeypatch.setattr(READY_FILTER, AsyncMock(return_value=[healthy, unreadable]))

    records, sink_id = capture_logs()
    try:
        selected = await WorkerLoadBalancer().select_best_worker(workers=[healthy, unreadable])
    finally:
        logger.remove(sink_id)

    assert selected is healthy
    # 认派发这一层自己那句话，而不是探测函数的 ERROR ——后者在"指标故障被 _fetch_resources
    # 吞回 None"时照样会打，拿它做判据会让这条用例在缺陷仍在时误绿（本用例第一版就是如此）。
    dispatch_alarm = "本轮派发已把它们排除"
    assert dispatch_alarm in records.text("ERROR"), "还有候选也不能把指标故障咽下去"
    assert "mn-unreadable" in records.text("ERROR")
    assert "mn-healthy" not in records.text("ERROR")


@pytest.mark.asyncio
async def test_all_healthy_dispatch_reports_no_metrics_failure(monkeypatch) -> None:
    """控制组：全都读得到时既能选中，也不许冒出任何指标故障告警。"""
    workers = [_worker(1, "mn-healthy-01"), _worker(2, "mn-healthy-02")]
    _install_redis(
        monkeypatch,
        hashes={
            "{antcode}:heartbeat:worker-1": heartbeat(IDLE_CPU),
            "{antcode}:heartbeat:worker-2": heartbeat(IDLE_CPU),
        },
    )
    monkeypatch.setattr(READY_FILTER, AsyncMock(return_value=workers))

    records, sink_id = capture_logs()
    try:
        selected = await WorkerLoadBalancer().select_best_worker(workers=workers)
    finally:
        logger.remove(sink_id)

    assert selected in workers
    assert records.text("ERROR") == ""


# --------------------------------------------------------------------------------------
# 4. "读不到" != "确实没上报过"
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_reported_worker_is_fail_closed_without_pretending_redis_failed(monkeypatch) -> None:
    """Redis 连得上、hash 是空的：这是关于这台机器的真实结论，不是基础设施故障。

    仍然 fail-closed 踢出候选（缺项补 100 是既有策略），但既不抛
    ``WorkerMetricsUnavailableError``，也不报 ERROR —— 否则真出 Redis 故障时没人会信告警。
    """
    silent = _worker(1, "mn-never-reported")
    _install_redis(monkeypatch, hashes={})

    records, sink_id = capture_logs()
    try:
        normalized = await probe_worker_resources(silent)
    finally:
        logger.remove(sink_id)

    assert normalized["cpu"] == PERCENT_FULL
    assert WorkerLoadBalancer().is_worker_available(silent, normalized) is False
    assert records.text("ERROR") == "", "没上报过不是基础设施故障，不许占用 ERROR"
    assert "mn-never-reported" in records.text("WARNING"), "但也不许一声不吭"
