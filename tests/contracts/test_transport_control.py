"""
Control-channel contract — `poll_control`, `ack_control`, `send_control_result`.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_poll_control_empty_returns_none(transport):
    """Polling an empty control queue must return None on timeout, not raise."""
    msg = await transport.poll_control(timeout=0.5)
    assert msg is None


async def test_poll_control_receives_targeted_message(transport, fresh_ids, redis_admin):
    """A control message pushed to the worker-specific stream must come
    back with a populated `control_type` and `receipt`."""
    if redis_admin is None:
        pytest.skip("control producer helper currently uses redis_admin")

    from antcode_worker.transport.base import ControlType

    keys = transport._test_keys  # type: ignore[attr-defined]
    stream = keys.control_stream(transport._worker_id)  # type: ignore[attr-defined]
    await redis_admin.xadd(
        stream,
        {
            "control_type": ControlType.CANCEL.value,
            "task_id": fresh_ids.task_id,
            "run_id": fresh_ids.run_id,
            "reason": "user-cancel",
        },
    )

    msg = await transport.poll_control(timeout=2.0)
    assert msg is not None
    assert msg.control_type == ControlType.CANCEL.value
    assert msg.task_id == fresh_ids.task_id
    assert msg.receipt, "poll_control() must populate a receipt for ack"


async def test_ack_control_consumes_message(transport, fresh_ids, redis_admin):
    """After ack_control(), a re-poll on the same group must not return
    the same message."""
    if redis_admin is None:
        pytest.skip("control producer helper currently uses redis_admin")

    from antcode_worker.transport.base import ControlType

    keys = transport._test_keys  # type: ignore[attr-defined]
    stream = keys.control_stream(transport._worker_id)  # type: ignore[attr-defined]
    await redis_admin.xadd(
        stream,
        {
            "control_type": ControlType.KILL.value,
            "task_id": fresh_ids.task_id,
            "run_id": fresh_ids.run_id,
            "reason": "ops-kill",
        },
    )

    msg = await transport.poll_control(timeout=2.0)
    assert msg is not None
    ok = await transport.ack_control(msg.receipt)
    assert ok is True

    again = await transport.poll_control(timeout=0.5)
    assert again is None


async def test_send_control_result_round_trips(transport, redis_admin):
    """send_control_result must write to the reply stream with success/error
    fields and the same request_id."""
    if redis_admin is None:
        pytest.skip("redis-only assertion on reply stream contents")

    ns = transport._test_namespace  # type: ignore[attr-defined]
    request_id = "req-test-001"
    reply_stream = f"{ns}:control:reply:{request_id}"

    ok = await transport.send_control_result(
        request_id=request_id,
        reply_stream=reply_stream,
        success=True,
        data={"ack": "ok"},
        error="",
    )
    assert ok is True

    entries = await redis_admin.xrange(reply_stream, count=5)
    assert len(entries) == 1
    _msg_id, fields = entries[0]
    assert fields["request_id"] == request_id
    assert fields["success"] == "true"
    assert "ack" in fields["data"]


async def test_send_control_result_failure_records_error(transport, redis_admin):
    """Failure case must set success=false and surface the error string."""
    if redis_admin is None:
        pytest.skip("redis-only assertion on reply stream contents")

    ns = transport._test_namespace  # type: ignore[attr-defined]
    request_id = "req-test-fail-002"
    reply_stream = f"{ns}:control:reply:{request_id}"

    ok = await transport.send_control_result(
        request_id=request_id,
        reply_stream=reply_stream,
        success=False,
        data=None,
        error="something went wrong",
    )
    assert ok is True

    entries = await redis_admin.xrange(reply_stream, count=5)
    assert len(entries) == 1
    _msg_id, fields = entries[0]
    assert fields["success"] == "false"
    assert fields["error"] == "something went wrong"
