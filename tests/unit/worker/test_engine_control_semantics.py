"""Engine 控制语义测试。

从 test_engine.py 拆出的两组语义：
- P1-DR-04: 运行时控制 deadline（transport 权威时钟）判定
- FN-01(c): 取消先于任务到达的 tombstone 结算
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_contracts.runtime_metadata import RUNTIME_DESCRIPTION_MAX_BYTES
from antcode_worker.engine.engine import Engine

FUTURE_RUNTIME_DEADLINE_MS = 4_102_444_800_000
RESULT_SEND_ATTEMPTS = 2  # 首次发送失败 + 重试成功，动作只执行一次


@pytest.fixture
def mock_transport():
    """模拟传输层（与 test_engine.py 的 TestEngine fixture 同构）"""
    transport = MagicMock()
    transport.poll_task = AsyncMock(return_value=None)
    transport.poll_control = AsyncMock(return_value=None)
    transport.report_result = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock(return_value=True)
    transport.ack_control = AsyncMock(return_value=True)
    # P1-DR-04: 运行时控制 deadline 判定改用 transport 权威时钟。
    transport.authoritative_now_ms = AsyncMock(return_value=1_000)
    transport._lease_id = "lease-test"
    return transport


@pytest.fixture
def mock_executor():
    """模拟执行器"""
    executor = MagicMock()
    executor.run = AsyncMock()
    executor.cancel = AsyncMock()
    return executor


class _ControlledClock:
    """P1-DR-04: 模拟 transport 权威时钟（authoritative_now_ms）的受控序列。"""

    def __init__(self, values_ms: tuple[int, ...], milestone: int, reached: asyncio.Event):
        self._values = iter(values_ms)
        self._milestone = milestone
        self._reached = reached
        self.calls = 0

    def next_ms(self) -> int:
        self.calls += 1
        if self.calls == self._milestone:
            self._reached.set()
        return next(self._values)


def _runtime_control(request_id: str, receipt: str, expires_at_ms: int = FUTURE_RUNTIME_DEADLINE_MS):
    return MagicMock(
        payload={
            "action": "get_platform_info",
            "request_id": request_id,
            "expires_at_ms": expires_at_ms,
        },
        receipt=receipt,
    )


@pytest.mark.asyncio
async def test_cancel_before_task_arrival_records_tombstone(mock_transport, mock_executor):
    """FN-01(c): 取消先于任务到达 → 记 tombstone 并返回 True（control 可 ACK）。"""
    engine = Engine(transport=mock_transport, executor=mock_executor)

    result = await engine.cancel("run-early", reason="user cancel")

    assert result is True
    assert await engine._cancel_tombstones.consume("run-early") is True
    # 消费后不可重复命中
    assert await engine._cancel_tombstones.consume("run-early") is False


@pytest.mark.asyncio
async def test_tombstoned_task_is_settled_as_cancelled_without_executing(mock_transport, mock_executor):
    """FN-01(c): tombstone 命中的任务按 CANCELLED 结算 + ACK，不执行。"""
    engine = Engine(transport=mock_transport, executor=mock_executor)
    await engine.cancel("run-early", reason="user cancel")
    report = AsyncMock()
    task_msg = MagicMock(task_id="task-1", receipt="receipt-1")

    with patch.object(engine, "_report_result_by_info", report):
        assert await engine._cancel_tombstones.consume("run-early") is True
        await engine._settle_tombstoned_task("run-early", task_msg)

    report.assert_awaited_once()
    kwargs = report.await_args.kwargs
    assert kwargs["run_id"] == "run-early"
    assert kwargs["receipt"] == "receipt-1"


@pytest.mark.asyncio
async def test_expired_tombstone_does_not_block_task(mock_transport, mock_executor):
    """过期 tombstone 不拦截任务（TTL 之外的同名 run 是新一次派发）。"""
    engine = Engine(transport=mock_transport, executor=mock_executor)
    engine._cancel_tombstones._entries["run-old"] = asyncio.get_event_loop().time() - 1.0

    assert await engine._cancel_tombstones.consume("run-old") is False


@pytest.mark.asyncio
async def test_runtime_control_retries_result_without_reexecuting_action(
    mock_transport,
    mock_executor,
    monkeypatch,
):
    mock_transport.send_control_result = AsyncMock(side_effect=[False, True])
    engine = Engine(transport=mock_transport, executor=mock_executor)
    engine._running = True
    action = AsyncMock(return_value={"platform": "test"})
    sleep = AsyncMock()
    monkeypatch.setattr("antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async", action)
    monkeypatch.setattr("antcode_worker.engine.engine.asyncio.sleep", sleep)
    control = MagicMock(
        payload={
            "action": "get_platform_info",
            "request_id": "req-1",
            "expires_at_ms": FUTURE_RUNTIME_DEADLINE_MS,
            "reply_stream": "reply-stream",
            "payload": {},
        },
        receipt="receipt-1",
    )

    await engine._handle_runtime_control(control)

    action.assert_awaited_once()
    assert mock_transport.send_control_result.await_count == RESULT_SEND_ATTEMPTS
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
async def test_runtime_update_rejects_oversized_metadata_before_uv_manager(
    mock_transport,
    mock_executor,
    monkeypatch,
):
    mock_transport.send_control_result = AsyncMock(return_value=True)
    update = AsyncMock()
    monkeypatch.setattr("antcode_worker.runtime.uv_manager.uv_manager.update_env", update)
    engine = Engine(transport=mock_transport, executor=mock_executor)
    control = MagicMock(
        payload={
            "action": "update_env",
            "request_id": "req-oversized",
            "expires_at_ms": FUTURE_RUNTIME_DEADLINE_MS,
            "payload": {
                "env_name": "private-test",
                "description": "界" * (RUNTIME_DESCRIPTION_MAX_BYTES // 3 + 1),
            },
        },
        receipt="receipt-oversized",
    )

    await engine._handle_runtime_control(control)

    update.assert_not_awaited()
    settlement = mock_transport.send_control_result.await_args.kwargs
    assert settlement["success"] is False
    assert "description UTF-8" in settlement["error"]


@pytest.mark.asyncio
async def test_expired_runtime_control_is_settled_without_executing_action(
    mock_transport,
    mock_executor,
    monkeypatch,
):
    mock_transport.send_control_result = AsyncMock(return_value=True)
    action = AsyncMock(return_value={"platform": "must-not-run"})
    monkeypatch.setattr("antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async", action)
    engine = Engine(transport=mock_transport, executor=mock_executor)
    control = MagicMock(
        payload={
            "action": "get_platform_info",
            "request_id": "req-expired",
            "expires_at_ms": 1,
        },
        receipt="receipt-expired",
    )

    await engine._handle_runtime_control(control)

    action.assert_not_awaited()
    result = mock_transport.send_control_result.await_args.kwargs
    assert result["success"] is False
    assert "已过期" in result["error"]


@pytest.mark.asyncio
async def test_runtime_control_expiring_while_waiting_for_semaphore_is_not_executed(
    mock_transport,
    mock_executor,
    monkeypatch,
):
    mock_transport.send_control_result = AsyncMock(return_value=True)
    action_started = asyncio.Event()
    second_precheck_complete = asyncio.Event()
    release_action = asyncio.Event()
    action = AsyncMock()

    async def blocking_action():
        action_started.set()
        await release_action.wait()
        return {"platform": "test"}

    action.side_effect = blocking_action
    # P1-DR-04: 过期判定改用 transport 权威时钟；按调用序喂时间：
    # 第 1/2 次（req-1 前置+信号量内）与第 3 次（req-2 前置，触发事件）
    # 均未过期，第 4 次（req-2 信号量内复检）已过 2_000ms deadline。
    clock = _ControlledClock((1_000, 1_000, 1_000, 3_000), 3, second_precheck_complete)
    mock_transport.authoritative_now_ms = AsyncMock(side_effect=clock.next_ms)
    monkeypatch.setattr("antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async", action)
    engine = Engine(transport=mock_transport, executor=mock_executor)

    first = asyncio.create_task(engine._handle_runtime_control(_runtime_control("req-1", "receipt-1", 2_000)))
    await action_started.wait()
    second = asyncio.create_task(engine._handle_runtime_control(_runtime_control("req-2", "receipt-2", 2_000)))
    await second_precheck_complete.wait()
    release_action.set()
    await asyncio.gather(first, second)

    action.assert_awaited_once()
    settlements = {
        call.kwargs["request_id"]: call.kwargs for call in mock_transport.send_control_result.await_args_list
    }
    assert settlements["req-1"]["success"] is True
    assert settlements["req-2"]["success"] is False
    assert "已过期" in settlements["req-2"]["error"]
