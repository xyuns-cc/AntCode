"""Direct Redis reclaim fencing and settlement recovery tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.transport.redis.direct_control import DirectLeaseGrant
from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer, ReclaimConfig, ReclaimedTask
from antcode_worker.transport.redis.transport import RedisTransport

EXPECTED_SETTLEMENT_EVALS = 4


@pytest.mark.asyncio
async def test_reclaimer_rechecks_generation_before_classification():
    redis = AsyncMock()
    redis.xpending_range.return_value = [
        {
            "message_id": "1-0",
            "consumer": "worker-1-lease-old",
            "time_since_delivered": 1,
            "times_delivered": 1,
        }
    ]
    redis.eval.return_value = [["1-0", ["task_id", "task-1"]]]
    guard = AsyncMock(side_effect=[True, True, True, False])
    reclaimer = PendingTaskReclaimer(
        redis,
        "worker-1",
        config=ReclaimConfig(max_reclaim_count=1),
        generation_guard=guard,
        current_consumer_name=lambda: "worker-1-lease-current",
    )

    with pytest.raises(RuntimeError, match="generation"):
        await reclaimer.reclaim_once()

    assert redis.xpending_range.await_count == 1
    assert reclaimer.stats.reclaim_errors == 1


@pytest.mark.asyncio
async def test_background_reclaim_failure_is_counted_once(monkeypatch):
    reclaimer = PendingTaskReclaimer(AsyncMock(), "worker-1")
    reclaimer._running = True
    reclaimer._do_reclaim = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    monkeypatch.setattr(
        "antcode_worker.transport.redis.reclaim.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)
    )

    await reclaimer._reclaim_loop()

    assert reclaimer.stats.reclaim_errors == 1


@pytest.mark.asyncio
async def test_dead_letter_rechecks_generation_between_write_and_ack():
    redis = AsyncMock()
    redis.eval.return_value = [1, "10-0"]
    guard = AsyncMock(side_effect=[True, True, False])
    reclaimer = PendingTaskReclaimer(redis, "worker-1", generation_guard=guard)

    with pytest.raises(RuntimeError, match="generation"):
        await reclaimer._move_to_dead_letter(
            "{antcode}:task:ready:worker-1",
            ReclaimedTask("1-0", {"task_id": "task-1"}, 1, 4),
        )

    redis.eval.assert_awaited_once()
    redis.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaimed_callback_is_not_invoked_after_generation_loss():
    callback = AsyncMock()
    reclaimer = PendingTaskReclaimer(
        AsyncMock(),
        "worker-1",
        generation_guard=AsyncMock(return_value=False),
        on_reclaimed=callback,
    )

    await reclaimer._deliver_reclaimed([ReclaimedTask("1-0", {"task_id": "task-1"}, 1, 1)])

    callback.assert_not_awaited()
    assert reclaimer.stats.reclaim_errors == 1


@pytest.mark.asyncio
async def test_lease_renew_scopes_task_consumer_to_new_generation():
    transport = RedisTransport(redis_url="redis://localhost/0", worker_id="worker-1")
    transport._lease_store = SimpleNamespace(policy=SimpleNamespace(renew_after_ms=10))
    transport._direct_control = SimpleNamespace(
        lease_renew=AsyncMock(
            return_value=DirectLeaseGrant(
                lease_id="lease-2", expires_at_ms=123, renew_after_ms=10, ttl_ms=30, revoked=False
            )
        ),
    )

    await transport.lease_renew("")

    transport._direct_control.lease_renew.assert_awaited_once_with("", None, {})
    assert transport._consumer_name == "worker-1"
    assert transport._task_consumer_name == "worker-1-lease-2"
    assert transport._control_consumer_name == "worker-1-lease-2"


@pytest.mark.asyncio
async def test_dead_letter_retry_after_ack_loss_uses_atomic_settlement_marker():
    source = "{antcode}:task:ready:worker-1"
    redis = AsyncMock()
    redis.eval.side_effect = [[1, "10-0"], RuntimeError("ACK response lost"), [0, "10-0"], 1]
    reclaimer = PendingTaskReclaimer(redis, "worker-1")
    task = ReclaimedTask("1-0", {"task_id": "task-1"}, 1, 4)

    with pytest.raises(RuntimeError, match="response lost"):
        await reclaimer._move_to_dead_letter(source, task)
    await reclaimer._move_to_dead_letter(source, task)

    assert redis.eval.await_count == EXPECTED_SETTLEMENT_EVALS
    first = redis.eval.await_args_list[0].args
    second = redis.eval.await_args_list[2].args
    assert first[2:4] == second[2:4]
    assert "{dlq}" in first[2]
    redis.xrange.assert_not_awaited()
    redis.xadd.assert_not_awaited()
    redis.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_dead_letter_accepts_zero_ack_only_when_source_left_pel():
    redis = AsyncMock()
    redis.eval.side_effect = [[1, "10-0"], 0]
    redis.xpending_range.return_value = []
    reclaimer = PendingTaskReclaimer(redis, "worker-1")

    await reclaimer._move_to_dead_letter(
        "{antcode}:task:ready:worker-1",
        ReclaimedTask("1-0", {"task_id": "task-1"}, 1, 4),
    )

    redis.xpending_range.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_observability_propagates_redis_failure():
    redis = AsyncMock()
    redis.xpending.side_effect = RuntimeError("redis unavailable")
    reclaimer = PendingTaskReclaimer(redis, "worker-1")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await reclaimer.get_pending_count()
    with pytest.raises(RuntimeError, match="redis unavailable"):
        await reclaimer.get_pending_summary()


@pytest.mark.asyncio
async def test_reclaimed_callback_failure_is_logged(monkeypatch):
    callback = AsyncMock(side_effect=RuntimeError("queue unavailable"))
    reset_recovery = MagicMock()
    observed_logger = MagicMock()
    reclaimer = PendingTaskReclaimer(
        AsyncMock(),
        "worker-1",
        on_reclaimed=callback,
        on_delivery_failed=reset_recovery,
    )
    monkeypatch.setattr("antcode_worker.transport.redis.reclaim.logger.exception", observed_logger)

    await reclaimer._deliver_reclaimed([ReclaimedTask("1-0", {"task_id": "task-1"}, 1, 1)])

    assert reclaimer.stats.reclaim_errors == 1
    reset_recovery.assert_called_once_with()
    observed_logger.assert_called_once()


@pytest.mark.asyncio
async def test_reclaim_retry_count_excludes_initial_delivery():
    reclaimer = PendingTaskReclaimer(
        AsyncMock(),
        "worker-1",
        config=ReclaimConfig(max_retries=1),
    )
    reclaimer._build_reclaimed_task = AsyncMock(
        side_effect=[
            ReclaimedTask("2-0", {}, 1, 2),
            ReclaimedTask("3-0", {}, 1, 3),
        ]
    )
    reclaimer._discard_exhausted = AsyncMock()

    reclaimed = await reclaimer._classify_claimed(
        "stream",
        [("2-0", {}), ("3-0", {})],
        "worker-current",
    )

    assert [task.message_id for task in reclaimed] == ["2-0"]
    reclaimer._discard_exhausted.assert_awaited_once()
    assert reclaimer._discard_exhausted.await_args.args[1].message_id == "3-0"


def test_requeue_marker_never_rebuilds_a_committed_message():
    from antcode_worker.transport.redis.task_settlement import _REQUEUE_OWNED_TASK_LUA

    marker_branch = _REQUEUE_OWNED_TASK_LUA.split("end\n\nlocal pending", 1)[0]
    assert "XRANGE" not in marker_branch
    assert "XADD" not in marker_branch


@pytest.mark.asyncio
async def test_requeue_rejects_corrupt_settlement_marker():
    from antcode_worker.transport.redis.task_settlement import requeue_owned_task

    redis = AsyncMock()
    redis.time.return_value = (1_000, 0)
    redis.eval.return_value = [-4, ""]

    with pytest.raises(RuntimeError, match="marker 损坏"):
        await requeue_owned_task(
            redis,
            stream_key="{antcode}:task:ready:worker-1",
            group="antcode-workers",
            message_id="1-0",
            consumer_name="worker-1-current",
            payload={"task_id": "task-1"},
        )
