"""Exact-message recovery tests for explicitly deferred Direct tasks."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from antcode_worker.transport.redis.deferred_recovery import DeferredTaskRecovery


def _recovery(redis, guard, callback, *, visibility=0, retry=0.001):
    return DeferredTaskRecovery(
        redis_provider=lambda: redis,
        consumer_group="workers",
        current_consumer_name=lambda: "worker-1-lease-1",
        generation_guard=guard,
        on_visible=callback,
        visibility_seconds=visibility,
        retry_seconds=retry,
    )


async def _wait_until_complete(recovery: DeferredTaskRecovery) -> None:
    async def complete() -> None:
        while recovery._tasks:
            await asyncio.sleep(0)

    await asyncio.wait_for(complete(), timeout=0.5)


@pytest.mark.asyncio
async def test_deferred_recovery_claims_only_registered_message_without_incrementing_delivery_count():
    redis = AsyncMock()
    redis.eval.return_value = [1, ["2-0", ["task_id", "task-2"]]]
    callback = AsyncMock()
    recovery = _recovery(redis, AsyncMock(return_value=True), callback)
    await recovery.start()

    await recovery.defer(
        stream_key="ready:worker-1",
        message_id="2-0",
        consumer_name="worker-1-lease-1",
    )
    await _wait_until_complete(recovery)
    await recovery.stop()

    args = redis.eval.await_args.args
    assert args[2:] == ("ready:worker-1", "workers", "2-0", "worker-1-lease-1")
    assert "RETRYCOUNT" in args[0]
    callback.assert_awaited_once_with("2-0", {"task_id": "task-2"})


@pytest.mark.asyncio
async def test_deferred_recovery_does_not_deliver_after_generation_changes():
    redis = AsyncMock()
    redis.eval.return_value = [1, ["2-0", ["task_id", "task-2"]]]
    callback = AsyncMock()
    guard = AsyncMock(side_effect=[True, False])
    recovery = _recovery(redis, guard, callback)
    await recovery.start()

    await recovery.defer(
        stream_key="ready:worker-1",
        message_id="2-0",
        consumer_name="worker-1-lease-1",
    )
    await _wait_until_complete(recovery)
    await recovery.stop()

    redis.eval.assert_awaited_once()
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_recovery_retries_visible_redis_failure():
    redis = AsyncMock()
    redis.eval.side_effect = [
        RuntimeError("redis unavailable"),
        [1, ["2-0", ["task_id", "task-2"]]],
    ]
    callback = AsyncMock()
    recovery = _recovery(redis, AsyncMock(return_value=True), callback)
    await recovery.start()

    await recovery.defer(
        stream_key="ready:worker-1",
        message_id="2-0",
        consumer_name="worker-1-lease-1",
    )
    await _wait_until_complete(recovery)
    await recovery.stop()

    assert redis.eval.await_count == 2
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_deferred_recovery_owner_change_is_terminal():
    redis = AsyncMock()
    redis.eval.return_value = [-2, []]
    callback = AsyncMock()
    recovery = _recovery(redis, AsyncMock(return_value=True), callback)
    await recovery.start()

    await recovery.defer(
        stream_key="ready:worker-1",
        message_id="2-0",
        consumer_name="worker-1-lease-1",
    )
    await _wait_until_complete(recovery)
    await recovery.stop()

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_recovery_stop_cancels_future_delivery():
    redis = AsyncMock()
    recovery = _recovery(
        redis,
        AsyncMock(return_value=True),
        AsyncMock(),
        visibility=30,
    )
    await recovery.start()
    await recovery.defer(
        stream_key="ready:worker-1",
        message_id="2-0",
        consumer_name="worker-1-lease-1",
    )

    await recovery.stop()

    assert recovery._tasks == {}
    redis.eval.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("visibility_seconds", -1),
        ("visibility_seconds", float("nan")),
        ("retry_seconds", 0),
        ("retry_seconds", float("inf")),
    ],
)
def test_deferred_recovery_rejects_invalid_delays(field, value):
    options = {field: value}
    with pytest.raises(ValueError):
        DeferredTaskRecovery(
            redis_provider=lambda: AsyncMock(),
            consumer_group="workers",
            current_consumer_name=lambda: "consumer",
            generation_guard=AsyncMock(return_value=True),
            on_visible=AsyncMock(),
            **options,
        )
