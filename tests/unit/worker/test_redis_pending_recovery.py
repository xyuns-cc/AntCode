"""Direct Redis task PEL recovery and dead-letter guarantees."""

from unittest.mock import AsyncMock

import pytest
from antcode_core.common.security.task_payload_envelope import seal_ready_payload
from antcode_worker.transport.redis.pending_recovery import PendingMessageRecovery
from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer, ReclaimConfig
from antcode_worker.transport.redis.transport import RedisTransport

AVAILABLE_CAPACITY = 2
RECLAIMED_QUEUE_CAPACITY = 3
WORKER_SECRET = "direct-pending-recovery-secret-material-0001"


def _task_fields(task_id: str) -> dict[str, object]:
    digest = "a" * 64
    return seal_ready_payload(
        {
            "task_id": task_id,
            "project_id": "project-1",
            "run_id": f"run-{task_id}",
            "dispatch_lease_id": "lease-1",
            "dispatch_lease_gen": "7",
            "source_bundle_uri": f"pgartifact://{digest}",
            "source_bundle_sha256": digest,
            "source_bundle_size": "1",
        },
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )


@pytest.mark.asyncio
async def test_pending_message_recovery_pages_until_consumer_pel_is_empty():
    redis = AsyncMock()
    redis.xreadgroup.side_effect = [
        [("stream", [("1-0", {"task_id": "one"}), ("2-0", {"task_id": "two"})])],
        [("stream", [("3-0", {"task_id": "three"})])],
        [],
    ]
    recovery = PendingMessageRecovery("stream", "group", page_size=2)

    deliveries = [await recovery.poll(redis, "consumer") for _ in range(4)]

    assert [item[0] if item else None for item in deliveries] == ["1-0", "2-0", "3-0", None]
    assert [call.kwargs["streams"] for call in redis.xreadgroup.await_args_list] == [
        {"stream": "0-0"},
        {"stream": "2-0"},
        {"stream": "3-0"},
    ]
    assert recovery.complete is True


@pytest.mark.asyncio
async def test_transport_recovers_own_task_pel_immediately_after_restart():
    stream = "{antcode}:task:ready:worker-1"
    redis = AsyncMock()
    redis.xreadgroup.return_value = [(stream, [("7-0", _task_fields("task-7"))])]
    transport = RedisTransport(
        redis_url="redis://localhost/0",
        worker_id="worker-1",
        task_payload_secret=WORKER_SECRET,
    )
    transport._redis = redis
    transport._running = True
    transport._lease_id = "lease-1"

    task = await transport.poll_task(timeout=0.1)

    assert task is not None
    assert task.task_id == "task-7"
    assert redis.xreadgroup.await_args.kwargs["streams"] == {stream: "0-0"}
    assert "block" not in redis.xreadgroup.await_args.kwargs


@pytest.mark.asyncio
async def test_reclaimer_pages_past_current_generation_to_claim_old_generation():
    redis = AsyncMock()
    current_consumer = "worker-1-lease-2"
    redis.xpending_range.side_effect = [
        [
            {
                "message_id": "100-0",
                "consumer": current_consumer,
                "time_since_delivered": 86_400_000,
                "times_delivered": 1,
            }
        ],
        [
            {
                "message_id": "200-0",
                "consumer": "worker-1-lease-1",
                "time_since_delivered": 1,
                "times_delivered": 1,
            }
        ],
        [
            {
                "message_id": "200-0",
                "consumer": current_consumer,
                "time_since_delivered": 0,
                "times_delivered": 2,
            }
        ],
    ]
    fields = [item for pair in _task_fields("task-200").items() for item in pair]
    redis.eval.return_value = [["200-0", fields]]
    reclaimer = PendingTaskReclaimer(
        redis,
        "worker-1",
        config=ReclaimConfig(max_reclaim_count=1),
        current_consumer_name=lambda: current_consumer,
    )

    tasks = await reclaimer.reclaim_once()

    assert [task.message_id for task in tasks] == ["200-0"]
    assert [call.kwargs["min"] for call in redis.xpending_range.await_args_list[:2]] == ["-", "(100-0"]
    assert redis.eval.await_args.args[4:7] == (current_consumer, "0", "200-0")


@pytest.mark.asyncio
async def test_reclaimer_never_self_claims_current_generation_long_task():
    redis = AsyncMock()
    current_consumer = "worker-1-lease-current"
    redis.xpending_range.side_effect = [
        [
            {
                "message_id": "1-0",
                "consumer": current_consumer,
                "time_since_delivered": 86_400_000,
                "times_delivered": 1,
            }
        ],
        [],
    ]
    reclaimer = PendingTaskReclaimer(
        redis,
        "worker-1",
        current_consumer_name=lambda: current_consumer,
    )

    assert await reclaimer.reclaim_once() == []
    redis.eval.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_idle_time_ms", -1),
        ("max_reclaim_count", 0),
        ("max_retries", -1),
        ("check_interval_seconds", 0),
        ("check_interval_seconds", float("nan")),
        ("check_interval_seconds", float("inf")),
        ("dead_letter_ttl_seconds", 0),
    ],
)
def test_reclaim_config_rejects_invalid_numeric_values(field, value):
    with pytest.raises(ValueError):
        ReclaimConfig(**{field: value})


@pytest.mark.asyncio
async def test_reclaimer_does_not_claim_when_lease_generation_is_stale():
    redis = AsyncMock()
    guard = AsyncMock(return_value=False)
    reclaimer = PendingTaskReclaimer(redis, "worker-1", generation_guard=guard)

    assert await reclaimer.reclaim_once() == []
    redis.xpending_range.assert_not_awaited()
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaimer_leaves_messages_in_redis_when_local_queue_is_full():
    redis = AsyncMock()
    reclaimer = PendingTaskReclaimer(
        redis,
        "worker-1",
        available_capacity=lambda: 0,
    )

    assert await reclaimer.reclaim_once() == []

    redis.xpending_range.assert_not_awaited()
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaimer_limits_claim_to_available_local_capacity():
    redis = AsyncMock()
    redis.xpending_range.return_value = []
    reclaimer = PendingTaskReclaimer(
        redis,
        "worker-1",
        config=ReclaimConfig(max_reclaim_count=10),
        available_capacity=lambda: AVAILABLE_CAPACITY,
    )

    assert await reclaimer.reclaim_once() == []

    assert redis.xpending_range.await_args.kwargs["count"] == AVAILABLE_CAPACITY


def test_transport_reclaimed_queue_is_bounded():
    transport = RedisTransport(
        redis_url="redis://localhost/0",
        worker_id="worker-1",
        reclaimed_queue_capacity=RECLAIMED_QUEUE_CAPACITY,
    )

    assert transport._reclaimed_queue.maxsize == RECLAIMED_QUEUE_CAPACITY
    assert transport._reclaimed_queue_available_capacity() == RECLAIMED_QUEUE_CAPACITY
