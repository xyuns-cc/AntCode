"""并发结算与 ownership 续租之间的 TOCTOU 回归（第五棒 P1）。

原缺陷：``_renew_active_run_ownership`` 遍历 ``StateManager.get_all()`` 的快照，
而每 renew 一个 run 都要 await 一次网络往返。快照里的另一个 run 完全可能在这期间
跑完 ``finish_settlement``（从 ``_runs`` 弹出）+ ``release``；轮到它时 renew 必然
返回 False，而「我自己放的」与「被别人抢走了」在 renew 的布尔返回值上不可区分
（``run_ownership_fence_lua._RENEW_SCRIPT`` 对 key 不存在与 key 属于他人都返回 0）。
老代码把二者都当成后者，处置方式是最重的一档——杀掉整个 Worker 进程。

12 个种子的爬取批次因此在 4 秒内把 Worker 打死：4 个成功、4 个被误标、4 个永久卡
queued。下面第一个用例就是这个场景的最小复现；第二、三个用例守住 fail-closed 方向
没有被放宽。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.ownership_fence import OwnershipFenceError
from antcode_worker.engine.state import RunState

SURVIVING_RUN = "batch-survivor"
SETTLING_RUN = "batch-settled"


def _engine() -> Engine:
    transport = MagicMock()
    transport.renew_run_ownership = AsyncMock(return_value=True)
    transport.release_run_ownership = AsyncMock(return_value=True)
    executor = MagicMock()
    executor.has_task = MagicMock(return_value=False)
    executor.cancel = AsyncMock(return_value=False)
    return Engine(transport=transport, executor=executor, max_concurrent=4)


async def _seed_running(engine: Engine, run_id: str) -> None:
    await engine.state_manager.add(run_id, task_id="task-1")
    await engine.state_manager.transition(run_id, RunState.PREPARING)
    await engine.state_manager.transition(run_id, RunState.RUNNING)


@pytest.mark.asyncio
async def test_run_finishing_mid_renewal_pass_does_not_fence_the_engine() -> None:
    """真实竞态：续租途中另一个 run 正常结算并释放，引擎不得自我停机。"""
    engine = _engine()
    # 先播 survivor：get_all() 保持插入序，所以快照会先轮到它，它的 renew 里
    # 才有机会让 settling run 走完结算——这正是线上 12 个种子并发时的顺序。
    await _seed_running(engine, SURVIVING_RUN)
    await _seed_running(engine, SETTLING_RUN)

    async def renew(run_id: str, _ttl_ms: int) -> bool:
        if run_id == SURVIVING_RUN:
            # 走到快照里的第一个 run 时，另一个 run 恰好跑完结算：先从 _runs
            # 弹出、再释放 fence —— 与 _report_result 的真实顺序一致。
            await engine.state_manager.remove(SETTLING_RUN)
            await engine._release_run_ownership(SETTLING_RUN)
            return True
        return False  # 它的 key 已经没了，Redis 只能回 False

    engine._transport.renew_run_ownership = AsyncMock(side_effect=renew)

    await engine._renew_active_run_ownership()

    assert engine._ownership_fenced is False
    engine._transport.release_run_ownership.assert_awaited_once_with(SETTLING_RUN)


@pytest.mark.asyncio
async def test_renew_failure_without_self_release_still_fences() -> None:
    """fail-closed 方向不变：没自己放过的 run 续租失败，照旧围栏。"""
    engine = _engine()
    await _seed_running(engine, SURVIVING_RUN)
    engine._transport.renew_run_ownership = AsyncMock(return_value=False)

    with pytest.raises(OwnershipFenceError, match=SURVIVING_RUN):
        await engine._renew_active_run_ownership()


@pytest.mark.asyncio
async def test_reclaimed_run_loses_its_release_record() -> None:
    """重投被重新 claim 后，旧的释放记录必须作废，否则等于给它开了永久豁免。"""
    engine = _engine()
    await engine._release_run_ownership(SETTLING_RUN)
    assert engine._released_ownership.was_released_by_self(SETTLING_RUN) is True

    engine._transport.claim_run_ownership = AsyncMock(return_value=True)
    await engine._claim_run_ownership(SETTLING_RUN)

    assert engine._released_ownership.was_released_by_self(SETTLING_RUN) is False
    await _seed_running(engine, SETTLING_RUN)
    engine._transport.renew_run_ownership = AsyncMock(return_value=False)
    with pytest.raises(OwnershipFenceError, match=SETTLING_RUN):
        await engine._renew_active_run_ownership()


@pytest.mark.asyncio
async def test_release_record_does_not_survive_into_the_next_pass() -> None:
    """账本只覆盖当轮：跨轮残留会让「真被抢」在下一轮被误放行。"""
    engine = _engine()
    await engine._release_run_ownership(SETTLING_RUN)
    await _seed_running(engine, SETTLING_RUN)
    engine._transport.renew_run_ownership = AsyncMock(return_value=False)

    with pytest.raises(OwnershipFenceError, match=SETTLING_RUN):
        await engine._renew_active_run_ownership()
