"""
Task-flow contract — `poll_task`, `ack_task`, `requeue_task`, `report_result`.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.asyncio


async def test_poll_empty_queue_returns_none(transport):
    """Polling an empty queue must return None within `timeout`, *not* raise."""
    msg = await transport.poll_task(timeout=0.5)
    assert msg is None


async def test_poll_returns_message_with_receipt(transport, task_producer, fresh_ids):
    """A pushed task must come back with a non-empty `receipt` for later ack."""
    await task_producer(
        transport,
        {
            "task_id": fresh_ids.task_id,
            "project_id": fresh_ids.project_id,
            "run_id": fresh_ids.run_id,
            "project_type": "code",
            "priority": "0",
            "timeout": "60",
        },
    )

    msg = await transport.poll_task(timeout=2.0)
    assert msg is not None
    assert msg.task_id == fresh_ids.task_id
    assert msg.run_id == fresh_ids.run_id
    # All implementations must include a non-empty receipt for ack/requeue.
    assert msg.receipt, "poll_task() must populate `receipt` so ack/requeue work"


async def test_ack_accepted_consumes_message(transport, task_producer, fresh_ids, redis_admin):
    """ack_task(accepted=True) must consume the message — a subsequent poll
    must NOT return it again (i.e. pending count goes to 0)."""
    await task_producer(
        transport,
        {
            "task_id": fresh_ids.task_id,
            "project_id": fresh_ids.project_id,
            "run_id": fresh_ids.run_id,
        },
    )

    msg = await transport.poll_task(timeout=2.0)
    assert msg is not None
    ok = await transport.ack_task(msg.receipt, accepted=True)
    assert ok is True

    # Same consumer must not see it again on a fresh poll.
    second = await transport.poll_task(timeout=0.5)
    assert second is None

    # For redis we can also assert pending=0 via XPENDING for stronger proof.
    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        stream = keys.task_ready_stream(transport._worker_id)  # type: ignore[attr-defined]
        pending = await redis_admin.xpending(stream, keys.consumer_group_name())
        # xpending returns {'pending': N, ...} on redis-py 7+
        if isinstance(pending, dict):
            assert pending.get("pending", 0) == 0
        else:
            assert pending[0] == 0


async def test_ack_rejected_requeues(transport, task_producer, fresh_ids, await_with_timeout):
    """ack_task(accepted=False, reason=...) must put the task back where
    another `poll_task` can retrieve it (possibly with a different receipt)."""
    await task_producer(
        transport,
        {
            "task_id": fresh_ids.task_id,
            "project_id": fresh_ids.project_id,
            "run_id": fresh_ids.run_id,
        },
    )

    first = await transport.poll_task(timeout=2.0)
    assert first is not None
    rejected = await transport.ack_task(first.receipt, accepted=False, reason="busy")
    assert rejected is True

    # The same logical task should come back on a subsequent poll.
    second = await await_with_timeout(transport.poll_task(timeout=2.0), timeout=3.0)
    assert second is not None
    assert second.task_id == fresh_ids.task_id


async def test_requeue_then_poll_returns_task(transport, task_producer, fresh_ids):
    """`requeue_task(receipt)` must make the task pollable again."""
    await task_producer(
        transport,
        {
            "task_id": fresh_ids.task_id,
            "project_id": fresh_ids.project_id,
            "run_id": fresh_ids.run_id,
        },
    )

    first = await transport.poll_task(timeout=2.0)
    assert first is not None
    ok = await transport.requeue_task(first.receipt, reason="manual-requeue")
    assert ok is True

    second = await transport.poll_task(timeout=2.0)
    assert second is not None
    assert second.task_id == fresh_ids.task_id


async def test_report_result_success(transport, fresh_ids, redis_admin):
    """report_result with status=success must land a single entry on the
    result stream."""
    from antcode_worker.transport.base import TaskResult

    result = TaskResult(
        run_id=fresh_ids.run_id,
        task_id=fresh_ids.task_id,
        status="success",
        exit_code=0,
        duration_ms=12.5,
        started_at=datetime.now(),
        finished_at=datetime.now(),
    )
    ok = await transport.report_result(result)
    assert ok is True

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        length = await redis_admin.xlen(keys.task_result_stream())
        assert length == 1


async def test_report_result_failure_preserves_exit_code(transport, fresh_ids, redis_admin):
    """report_result with status=failed must preserve exit_code & error_message."""
    from antcode_worker.transport.base import TaskResult

    result = TaskResult(
        run_id=fresh_ids.run_id,
        task_id=fresh_ids.task_id,
        status="failed",
        exit_code=137,
        error_message="OOM kill",
    )
    ok = await transport.report_result(result)
    assert ok is True

    if redis_admin is not None:
        keys = transport._test_keys  # type: ignore[attr-defined]
        entries = await redis_admin.xrange(keys.task_result_stream(), count=10)
        assert len(entries) == 1
        _msg_id, fields = entries[0]
        assert fields["status"] == "failed"
        assert fields["exit_code"] == "137"
        assert fields["error_message"] == "OOM kill"
