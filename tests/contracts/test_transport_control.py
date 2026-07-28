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


async def test_poll_control_receives_targeted_message(transport, fresh_ids, contract_probe):
    """A control message pushed to the worker-specific stream must come
    back with a populated `control_type` and `receipt`."""
    from antcode_worker.transport.base import ControlType

    await contract_probe.push_control(
        control_type=ControlType.CANCEL.value,
        task_id=fresh_ids.task_id,
        run_id=fresh_ids.run_id,
        reason="user-cancel",
    )

    msg = await transport.poll_control(timeout=2.0)
    assert msg is not None
    assert msg.control_type == ControlType.CANCEL.value
    assert msg.task_id == fresh_ids.task_id
    assert msg.receipt, "poll_control() must populate a receipt for ack"


async def test_ack_control_consumes_message(transport, fresh_ids, contract_probe):
    """After ack_control(), a re-poll on the same group must not return
    the same message."""
    from antcode_worker.transport.base import ControlType

    await contract_probe.push_control(
        control_type=ControlType.KILL.value,
        task_id=fresh_ids.task_id,
        run_id=fresh_ids.run_id,
        reason="ops-kill",
    )

    msg = await transport.poll_control(timeout=2.0)
    assert msg is not None
    ok = await transport.ack_control(msg.receipt)
    assert ok is True

    again = await transport.poll_control(timeout=0.5)
    assert again is None


async def test_send_control_result_round_trips(transport, contract_probe, fresh_ids):
    """send_control_result must write to the reply stream with success/error
    fields and the same request_id."""
    from antcode_core.infrastructure.redis import runtime_control_request_id

    request_id = runtime_control_request_id(fresh_ids.worker_id, "1" * 32)
    reply_stream = contract_probe.reply_stream(request_id)
    await contract_probe.push_runtime_control(
        request_id=request_id,
        action="list_envs",
        payload={"scope": "shared"},
    )
    control = await transport.poll_control(timeout=2.0)
    assert control is not None

    ok = await transport.send_control_result(
        request_id=request_id,
        reply_stream=reply_stream,
        success=True,
        receipt=control.receipt,
        data={"ack": "ok"},
        error="",
    )
    assert ok is True

    fields = await contract_probe.control_result(request_id)
    assert fields["request_id"] == request_id
    assert fields["success"] == "true"
    assert fields.get("error", "") == ""
    assert fields["data"] == '{"ack": "ok"}'


async def test_send_control_result_failure_records_error(transport, contract_probe, fresh_ids):
    """Failure case must set success=false and surface the error string."""
    from antcode_core.infrastructure.redis import runtime_control_request_id

    request_id = runtime_control_request_id(fresh_ids.worker_id, "2" * 32)
    reply_stream = contract_probe.reply_stream(request_id)
    await contract_probe.push_runtime_control(
        request_id=request_id,
        action="get_platform_info",
        payload={},
    )
    control = await transport.poll_control(timeout=2.0)
    assert control is not None

    ok = await transport.send_control_result(
        request_id=request_id,
        reply_stream=reply_stream,
        success=False,
        receipt=control.receipt,
        data=None,
        error="something went wrong",
    )
    assert ok is True

    fields = await contract_probe.control_result(request_id)
    assert fields["request_id"] == request_id
    assert fields["success"] == "false"
    assert fields["error"] == "something went wrong"
    assert fields["data"] == "null"
