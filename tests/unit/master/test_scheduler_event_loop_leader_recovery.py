import importlib
import time
from unittest.mock import AsyncMock

import pytest
from antcode_core.infrastructure.redis.stream_client import StreamMessage
from antcode_master.control.scheduler_event_loop import SchedulerEventLoop

event_loop_module = importlib.import_module("antcode_master.control.scheduler_event_loop")
EXPECTED_READ_CALLS = 2


def _message(msg_id: str) -> StreamMessage:
    return StreamMessage(
        msg_id=msg_id,
        data={"event": "batch_started", "batch_id": "batch-1"},
        stream_key="scheduler-events",
    )


def _loop_with_client() -> tuple[SchedulerEventLoop, AsyncMock]:
    loop = SchedulerEventLoop(idle_sleep=0)
    client = AsyncMock()
    loop._stream_client = client
    return loop, client


@pytest.mark.asyncio
async def test_standby_does_not_read_or_ack_scheduler_events(monkeypatch) -> None:
    loop, client = _loop_with_client()
    monkeypatch.setattr(event_loop_module, "ensure_leader", AsyncMock(return_value=False))

    await loop._run_iteration()

    client.ensure_group.assert_not_awaited()
    client.xreadgroup.assert_not_awaited()
    client.xautoclaim.assert_not_awaited()
    client.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_leader_loss_after_read_leaves_message_pending(monkeypatch) -> None:
    loop, client = _loop_with_client()
    loop._last_pending_check = time.monotonic()
    message = _message("1-0")
    client.xreadgroup.return_value = [message]
    authority = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(event_loop_module, "ensure_leader", authority)
    loop._handle_message = AsyncMock()

    await loop._run_iteration()

    loop._handle_message.assert_not_awaited()
    client.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_leader_loss_after_processing_skips_ack(monkeypatch) -> None:
    loop, client = _loop_with_client()
    loop._last_pending_check = time.monotonic()
    client.xreadgroup.return_value = [_message("2-0")]
    authority = AsyncMock(side_effect=[True, True, False])
    monkeypatch.setattr(event_loop_module, "ensure_leader", authority)
    loop._handle_message = AsyncMock()

    await loop._run_iteration()

    loop._handle_message.assert_awaited_once()
    client.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_pending_check_preempts_continuous_new_messages() -> None:
    loop, client = _loop_with_client()
    fresh = _message("3-0")
    pending = _message("1-0")
    claimed = _message("2-0")

    async def read_group(**kwargs):
        return [pending] if kwargs.get("read_pending") else [fresh]

    client.xreadgroup.side_effect = read_group
    client.xautoclaim.return_value = ("0-0", [claimed], [])
    loop._last_pending_check = time.monotonic()
    assert await loop._read_messages() == [fresh]

    loop._last_pending_check = 0.0
    recovered = await loop._read_messages()

    assert [message.msg_id for message in recovered] == ["1-0", "2-0"]
    assert client.xreadgroup.await_count == EXPECTED_READ_CALLS
    assert client.xreadgroup.await_args.kwargs["read_pending"] is True
    client.xautoclaim.assert_awaited_once()


@pytest.mark.asyncio
async def test_autoclaimed_messages_are_processed_and_acked(monkeypatch) -> None:
    loop, client = _loop_with_client()
    claimed = _message("4-0")
    client.xreadgroup.return_value = []
    client.xautoclaim.return_value = ("0-0", [claimed], [])
    monkeypatch.setattr(event_loop_module, "ensure_leader", AsyncMock(return_value=True))
    loop._handle_message = AsyncMock()
    loop._ack_messages = AsyncMock()

    await loop._run_iteration()

    loop._handle_message.assert_awaited_once_with(claimed.data)
    loop._ack_messages.assert_awaited_once_with([claimed.msg_id])
