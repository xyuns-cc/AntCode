"""P1-FN-04 回归:control cancel 的 False/异常路径要正确处理 ACK。

缺陷 P1-FN-04：
engine._control_loop 之前:
- 丢弃 Engine.cancel() 返回值
- 丢弃 executor.cancel() 返回值
- 缺 target 时静默 ACK

修复后不变量:
1. control cancel 缺 target: 记录 warn + ACK(否则 poison PEL 无限重投)
2. cancel 返回 False(终态): 记录 info + ACK(终态幂等)
3. cancel 抛异常: NOT ACK, PEL 保留供 reclaim
4. executor.cancel 真实失败: engine.cancel 抛错，control 不 ACK
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.domain.errors import CancellationError
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.state import RunState


class _FakeControl:
    def __init__(self, *, control_type: str, receipt: str, run_id: str = "", task_id: str = "", reason: str = ""):
        self.control_type = control_type
        self.receipt = receipt
        self.run_id = run_id
        self.task_id = task_id
        self.reason = reason
        self.payload = None


def _make_engine_and_transport():
    transport = MagicMock()
    transport.is_connected = True
    transport.poll_control = AsyncMock(return_value=None)
    transport.ack_control = AsyncMock(return_value=True)
    executor = MagicMock()
    engine = Engine(transport=transport, executor=executor, max_concurrent=1)
    return engine, transport


@pytest.mark.asyncio
async def test_control_cancel_empty_target_still_acks():
    """P1-FN-04:缺 target 的 cancel 也要 ACK 否则 poison PEL。"""
    engine, transport = _make_engine_and_transport()
    engine.cancel = AsyncMock(return_value=True)  # type: ignore[method-assign]

    control = _FakeControl(control_type="cancel", receipt="pel-1", run_id="", task_id="")
    controls = [control, None]
    transport.poll_control = AsyncMock(side_effect=lambda **_: controls.pop(0))
    engine._running = True

    async def _stop_after_one_iter():
        # 让 _control_loop 跑一轮就退出
        pass

    # 单步跑一轮 loop 而不是 create_task
    control_iter = iter([control])

    async def one_poll(**_):
        try:
            return next(control_iter)
        except StopIteration:
            engine._running = False
            return None

    transport.poll_control = one_poll
    await engine._control_loop()

    engine.cancel.assert_not_awaited()  # 缺 target,不调 cancel
    transport.ack_control.assert_awaited_once_with("pel-1")  # 但仍 ACK


@pytest.mark.asyncio
async def test_control_cancel_terminal_state_returns_false_and_still_acks():
    """P1-FN-04:cancel 返回 False(终态)时仍 ACK(幂等安全)。"""
    engine, transport = _make_engine_and_transport()
    engine.cancel = AsyncMock(return_value=False)  # type: ignore[method-assign]

    control = _FakeControl(control_type="cancel", receipt="pel-2", run_id="r-1")
    control_iter = iter([control])

    async def one_poll(**_):
        try:
            return next(control_iter)
        except StopIteration:
            engine._running = False
            return None

    transport.poll_control = one_poll
    engine._running = True

    await engine._control_loop()

    engine.cancel.assert_awaited_once_with("r-1", reason="cancel")
    transport.ack_control.assert_awaited_once_with("pel-2")  # False 也 ACK


@pytest.mark.asyncio
async def test_control_ack_false_is_not_silently_accepted():
    engine, transport = _make_engine_and_transport()
    engine.cancel = AsyncMock(return_value=False)  # type: ignore[method-assign]
    transport.ack_control.return_value = False
    control = _FakeControl(control_type="cancel", receipt="pel-ack-failed", run_id="r-1")

    with pytest.raises(RuntimeError, match="ACK 失败"):
        await engine._dispatch_control(control)

    transport.ack_control.assert_awaited_once_with("pel-ack-failed")


@pytest.mark.asyncio
async def test_control_cancel_raises_does_not_ack():
    """P1-FN-04 关键:cancel 抛异常 → 不 ACK(PEL 保留给 reclaim 重投)。"""
    engine, transport = _make_engine_and_transport()
    engine.cancel = AsyncMock(side_effect=RuntimeError("cancel exploded"))  # type: ignore[method-assign]

    control = _FakeControl(control_type="cancel", receipt="pel-3", run_id="r-1")
    control_iter = iter([control])

    async def one_poll(**_):
        try:
            return next(control_iter)
        except StopIteration:
            engine._running = False
            return None

    transport.poll_control = one_poll
    engine._running = True

    await engine._control_loop()

    engine.cancel.assert_awaited_once_with("r-1", reason="cancel")
    transport.ack_control.assert_not_awaited()  # 异常路径不 ACK


@pytest.mark.asyncio
async def test_engine_cancel_running_with_executor_missing_returns_true_with_warn():
    """P1-FN-04:executor 内已无 run(自然结束)时,engine.cancel 仍返回 True 走结算。"""
    engine, transport = _make_engine_and_transport()

    # 注入一个 RUNNING run
    await engine._state_manager.add("r-1", task_id="t-1")
    await engine._state_manager.transition("r-1", RunState.QUEUED)
    await engine._state_manager.transition("r-1", RunState.PREPARING)
    await engine._state_manager.transition("r-1", RunState.RUNNING)

    # executor.cancel 返回 False 且 is_running=False (run 已自然结束)
    engine._executor = MagicMock()
    engine._executor.has_task = MagicMock(return_value=False)  # 已消失
    engine._executor.cancel = AsyncMock(return_value=False)

    result = await engine.cancel("r-1", reason="test-warn-path")

    assert result is True  # engine.cancel 仍返回 True(状态已推动)
    engine._executor.cancel.assert_awaited_once_with("r-1")
    info = await engine._state_manager.get("r-1")
    assert info is not None
    assert info.state == RunState.CANCELLING


@pytest.mark.asyncio
async def test_engine_cancel_kill_failure_raises_p1_gw_05_round6():
    """P1-GW-05:is_running=True + cancel=False = kill 真失败并显式抛错。

    审查:cancel/kill 失败可能仍报告成功 → 旧子进程与 L2 接管者双执行。
    修:executor 内仍有 run 但 cancel 返回 False,视为 kill 真失败,不能
    报告取消成功让上游继续 ACK,而应返 False 让 control cancel 走 PEL 重投。
    """
    engine, transport = _make_engine_and_transport()

    await engine._state_manager.add("r-1", task_id="t-1")
    await engine._state_manager.transition("r-1", RunState.QUEUED)
    await engine._state_manager.transition("r-1", RunState.PREPARING)
    await engine._state_manager.transition("r-1", RunState.RUNNING)

    # is_running=True (executor 内仍有 run) + cancel=False = kill 真失败
    engine._executor = MagicMock()
    engine._executor.has_task = MagicMock(return_value=True)  # 未消失
    engine._executor.cancel = AsyncMock(return_value=False)  # kill 失败

    with pytest.raises(CancellationError, match="未能终止"):
        await engine.cancel("r-1", reason="test-kill-fail")


@pytest.mark.asyncio
async def test_control_cancel_kill_failure_does_not_ack():
    """运行中 kill 失败必须保留 control receipt，不能 ACK 虚假成功。"""
    engine, transport = _make_engine_and_transport()
    await engine._state_manager.add("r-1", task_id="t-1")
    await engine._state_manager.transition("r-1", RunState.QUEUED)
    await engine._state_manager.transition("r-1", RunState.PREPARING)
    await engine._state_manager.transition("r-1", RunState.RUNNING)
    engine._executor.has_task = MagicMock(return_value=True)
    engine._executor.cancel = AsyncMock(return_value=False)
    control = _FakeControl(control_type="kill", receipt="pel-kill", run_id="r-1")

    with pytest.raises(CancellationError, match="未能终止"):
        await engine._dispatch_control(control)

    transport.ack_control.assert_not_awaited()
