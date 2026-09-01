"""一台 Worker 的 ``metrics`` 列坏了，不许掀掉整轮派发，也不许被悄悄折算成一份指标。

``workers.metrics`` 是 jsonb，由 Worker 二进制写、由控制面读，实际存进数组或被二次编码成
字符串都发生过（87cf267 已在只读回显那一侧钉过同样的形状）。同一个坏值从前在两处得到两种
处理：

- ``probe_worker_resources`` 用 ``isinstance(..., Mapping)`` 判它"不是落库指标"，改读 Redis
  心跳，正常返回；
- 派发与排名两处却把它直接喂给 ``dict.update``。``[{"cpu": 1}]`` / ``'{"cpu": 1}'`` 抛
  ``ValueError``，掀掉 ``select_best_worker`` 整轮（异常**不**落进 ``dispatch_batch`` 自己的
  兜底——选节点发生在那个 ``try`` 之前，所以它是从 ``dispatch_batch`` 里裸抛出去的）；而
  ``["ab", "cd"]`` 这种更坏，它一声不响被拆成 ``{"a": "b", "c": "d"}`` 混进指标。

这里钉五件事：

1. 一台坏列不牵连同轮的其他机器，也不牵连它自己（心跳读得回来就照常派）；
2. **必须失败臂**：坏列不是"放行"的理由——心跳说它满了就还得被硬门禁拦下；
3. **控制组**：全都是正常 dict 时照常选中，且不许冒出任何"列坏了"的告警；
4. ``dispatch_batch`` / ``/workers/load/ranking`` 两个真入口都不再抛（排名侧另有一处同族
   不一致：它只判 ``Exception``，漏掉 ``gather`` 原样返回的 ``CancelledError``）；
5. 坏列不许被静默折算成指标（挡住"用 except ValueError 包一层"这类假修复）。

**证伪方式**：把 ``collect_dispatch_candidates`` 里的打分对象换回
``merge_worker_metrics(worker.metrics, probed)``，1/2(部分)/4/5 的派发侧变红；把
``get_workers_ranking`` 的 ``persisted_worker_metrics(worker)`` 换回 ``worker.metrics``，
排名侧变红；把排名侧的 ``isinstance(..., BaseException)`` 改回 ``Exception``，取消探测那条
变红；删 ``persisted_worker_metrics`` 里的 WARNING，第 3 组的点名判据变红。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.workers.worker_dispatcher import (
    WorkerLoadBalancer,
    WorkerTaskDispatcher,
)
from antcode_core.application.services.workers.worker_load_score import PERCENT_FULL, calculate_load_score
from antcode_core.application.services.workers.worker_metrics import normalize_worker_metrics
from antcode_core.application.services.workers.worker_resource_probe import merge_worker_metrics
from loguru import logger

from tests.unit.core.broken_metrics_column_support import (
    BUSY_CPU,
    COLUMN_ALARM,
    IDLE_CPU,
    LIST_COLUMN,
    MAX_TASKS,
    PAIRABLE_COLUMN,
    STR_COLUMN,
    capture_logs,
    heartbeat,
    install_workers,
    worker,
)

# --------------------------------------------------------------------------------------
# 1. 一台坏列不牵连别人，也不牵连它自己
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", [LIST_COLUMN, STR_COLUMN])
@pytest.mark.asyncio
async def test_a_broken_column_does_not_take_down_the_rest_of_the_round(monkeypatch, column) -> None:
    """坏列那台自己忙着，同轮的好机器必须照常被选中——而不是整轮抛 ValueError。

    只参数化会抛的两种形状。``PAIRABLE_COLUMN`` 在派发侧修复前后都绿（它不抛，折算出的
    杂键又不被任何判据读到），放在这里会是一条假绿；它由排名侧那条静默污染用例负责。
    """
    broken, healthy = worker(1, "mn-broken", column), worker(2, "mn-healthy", {})
    install_workers(monkeypatch, [broken, healthy], cpu_by_name={"mn-broken": BUSY_CPU})

    selected = await WorkerLoadBalancer().select_best_worker(workers=[broken, healthy])

    assert selected is healthy


@pytest.mark.asyncio
async def test_the_worker_with_the_broken_column_is_still_dispatchable_from_its_heartbeat(monkeypatch) -> None:
    """别把"不炸"做成"把它踢掉"：落库列坏了，Redis 心跳仍是一份独立且可信的读数。"""
    broken = worker(1, "mn-broken", LIST_COLUMN)
    install_workers(monkeypatch, [broken])

    assert await WorkerLoadBalancer().select_best_worker(workers=[broken]) is broken


# --------------------------------------------------------------------------------------
# 2. 必须失败臂：坏列不是放行的理由
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_busy_worker_is_still_rejected_even_when_its_column_is_broken(monkeypatch) -> None:
    """心跳说它 CPU 打满了就得被硬门禁拦下，不许因为"列坏了跳过校验"而放行。"""
    broken = worker(1, "mn-broken-busy", LIST_COLUMN)
    install_workers(monkeypatch, [broken], cpu_by_name={"mn-broken-busy": BUSY_CPU})

    assert await WorkerLoadBalancer().select_best_worker(workers=[broken]) is None


# --------------------------------------------------------------------------------------
# 3. 控制组 + 点名：全好时不许告警，坏时必须说得出是哪台、坏成什么样
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_healthy_columns_select_without_any_column_alarm(monkeypatch) -> None:
    """控制组：列都正常时既选得中，也不许冒出"列坏了"这句话。"""
    workers = [worker(1, "mn-ok-01", {}), worker(2, "mn-ok-02", {"cpu": IDLE_CPU})]
    install_workers(monkeypatch, workers)

    records, sink_id = capture_logs()
    try:
        selected = await WorkerLoadBalancer().select_best_worker(workers=workers)
    finally:
        logger.remove(sink_id)

    assert selected in workers
    assert COLUMN_ALARM not in records.text()


@pytest.mark.asyncio
async def test_the_broken_column_is_named_with_its_actual_shape(monkeypatch) -> None:
    """坏列必须点名到机器和实际类型，否则"是谁写的这一列"查不下去。"""
    broken = worker(1, "mn-broken", LIST_COLUMN)
    install_workers(monkeypatch, [broken])

    records, sink_id = capture_logs()
    try:
        await WorkerLoadBalancer().select_best_worker(workers=[broken])
    finally:
        logger.remove(sink_id)

    warnings = records.text("WARNING")
    assert COLUMN_ALARM in warnings
    assert "mn-broken" in warnings
    assert "list" in warnings


# --------------------------------------------------------------------------------------
# 4. 两个真入口都不再抛
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_batch_returns_a_result_instead_of_raising(monkeypatch) -> None:
    """选节点在 ``dispatch_batch`` 的 try 之前，所以从前那个 ValueError 是裸抛出去的。

    这里认"走到了选节点之后"这个信号：拿到的是结构化结果，且理由是心跳过期而不是坏列。
    """
    broken = worker(1, "mn-broken", LIST_COLUMN)
    install_workers(monkeypatch, [broken])

    result = await WorkerTaskDispatcher().dispatch_batch(tasks=[{"task_id": "t1", "project_id": "p1"}])

    assert result.error == f"Worker 未在线: {broken.name}"


@pytest.mark.asyncio
async def test_load_ranking_survives_a_broken_column(monkeypatch) -> None:
    """``GET /workers/load/ranking`` 从前会被一台坏列打成 500，同页的好机器一起陪葬。"""
    broken, healthy = worker(1, "mn-broken", LIST_COLUMN), worker(2, "mn-healthy", {})
    install_workers(monkeypatch, [broken, healthy])

    rankings = await WorkerLoadBalancer().get_workers_ranking()

    by_name = {row["name"]: row for row in rankings}
    assert set(by_name) == {"mn-broken", "mn-healthy"}
    assert by_name["mn-broken"]["metrics"]["cpu"] == float(IDLE_CPU)
    assert by_name["mn-broken"]["available"] is True


@pytest.mark.asyncio
async def test_load_ranking_treats_a_cancelled_probe_as_no_metrics(monkeypatch) -> None:
    """探测任务被取消同样不许打成 500。

    ``_refresh_resources`` 里的探测是共享 inflight task，一个调用方断开就会把它取消，
    同时在等的其他调用方拿到 ``CancelledError``。它是 ``BaseException`` 而不是
    ``Exception``，``gather(return_exceptions=True)`` 照样原样放进结果列表 —— 排名侧从前
    只判 ``Exception``，于是它漏进合并被 ``dict.update`` 打爆；派发侧判的一直是
    ``BaseException``。同一个值两处两种口径。
    """
    node = worker(1, "mn-cancelled", {})
    install_workers(monkeypatch, [node])
    balancer = WorkerLoadBalancer()
    monkeypatch.setattr(balancer, "_refresh_resources", AsyncMock(side_effect=asyncio.CancelledError()))

    row = (await balancer.get_workers_ranking())[0]

    assert row["available"] is False
    assert row["load_score"] == PERCENT_FULL
    assert row["metrics"] == {}


# --------------------------------------------------------------------------------------
# 5. 坏列不许被静默折算成指标
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pairable_string_list_is_not_folded_into_metrics(monkeypatch) -> None:
    """``["cp", "me"]`` 从前不抛，而是被 ``dict.update`` 拆成 ``{"c": "p", "m": "e"}``。

    这条同时挡住"拿 ``except ValueError`` 包一层"这类假修复——那种修法对本形状仍然无效。
    """
    broken = worker(1, "mn-pairable", PAIRABLE_COLUMN)
    install_workers(monkeypatch, [broken])

    metrics = (await WorkerLoadBalancer().get_workers_ranking())[0]["metrics"]

    assert set(metrics) == set(normalize_worker_metrics({}))


# --------------------------------------------------------------------------------------
# 非证伪项
# --------------------------------------------------------------------------------------


def test_merging_the_persisted_column_never_moved_a_dispatch_verdict() -> None:
    """**非证伪项**：修复前后都绿。

    它不刻画修复，而是钉住"派发侧为什么可以直接把落库列从合并里删掉"的依据：判据只读
    ``normalize_worker_metrics`` 的那六个键，而探测读数一定齐备、又恰好覆盖掉落库的同名
    键，所以落库那一层对结论的贡献恒为零。哪天新增判据读了第七个键，这条会先红。
    """
    persisted = {"cpu": BUSY_CPU, "taskCount": MAX_TASKS, "uptime": MAX_TASKS}
    probed = normalize_worker_metrics(heartbeat(IDLE_CPU))
    balancer, node = WorkerLoadBalancer(), worker(1, "mn-any", persisted)

    merged = merge_worker_metrics(persisted, probed)

    assert calculate_load_score(merged) == calculate_load_score(probed)
    assert balancer.is_worker_available(node, merged) == balancer.is_worker_available(node, probed)
    assert set(merged) - set(probed) == {"taskCount", "uptime"}
