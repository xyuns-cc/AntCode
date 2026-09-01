"""Durable cleanup of retry queue entries after task deletion."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.scheduler.retry_queue import RetryQueueBackend


@pytest.mark.asyncio
async def test_cancel_task_removes_only_matching_pending_entries() -> None:
    matching = json.dumps({"task_id": 7, "run_id": "run-7"})
    other = json.dumps({"task_id": 8, "run_id": "run-8"})
    redis = AsyncMock()
    redis.zrange.return_value = [matching, other]
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[1, 1])
    redis.pipeline = MagicMock(return_value=pipeline)
    backend = RetryQueueBackend()
    backend._get_redis = AsyncMock(return_value=redis)

    removed = await backend.cancel_task(7)

    assert removed == 1
    pipeline.zrem.assert_called_once_with(backend.pending_key(), matching)
    pipeline.hdel.assert_called_once_with(backend.attempts_key(), "run-7")
    pipeline.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_task_exposes_redis_failure() -> None:
    backend = RetryQueueBackend()
    backend._get_redis = AsyncMock(side_effect=RuntimeError("redis unavailable"))

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await backend.cancel_task(7)
