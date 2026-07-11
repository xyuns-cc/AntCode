from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.scheduler.retry_service import _RetryQueueBackend


@pytest.mark.asyncio
async def test_retry_ack_exposes_missing_processing_entry():
    redis = AsyncMock()
    redis.hdel.return_value = 0
    backend = _RetryQueueBackend()
    backend._get_redis = AsyncMock(return_value=redis)

    with pytest.raises(RuntimeError, match="did not remove"):
        await backend.ack("payload")


@pytest.mark.asyncio
async def test_retry_requeue_exposes_non_atomic_completion_failure():
    redis = AsyncMock()
    pipe = AsyncMock()
    pipe.zadd = MagicMock()
    pipe.hdel = MagicMock()
    pipe.execute.return_value = [1, 0]
    redis.pipeline = MagicMock(return_value=pipe)
    backend = _RetryQueueBackend()
    backend._get_redis = AsyncMock(return_value=redis)

    with pytest.raises(RuntimeError, match="did not clear"):
        await backend.requeue("payload")
