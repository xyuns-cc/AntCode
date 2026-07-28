from unittest.mock import AsyncMock

import pytest
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream


@pytest.mark.asyncio
async def test_trim_uses_minimum_pending_id():
    redis = AsyncMock()
    redis.xinfo_groups.return_value = [
        {"name": "workers", "last-delivered-id": "9-0"},
    ]
    redis.xpending.return_value = {"pending": 2, "min": "4-0"}
    redis.xtrim.return_value = 3

    trimmed = await trim_acknowledged_stream(redis, "stream", "workers")

    assert trimmed == 3
    redis.xtrim.assert_awaited_once_with(
        "stream",
        minid="4-0",
        approximate=True,
    )


@pytest.mark.asyncio
async def test_trim_without_pending_uses_last_delivered_id():
    redis = AsyncMock()
    redis.xinfo_groups.return_value = [
        {"name": "workers", "last-delivered-id": "7-2"},
    ]
    redis.xpending.return_value = {"pending": 0, "min": None}

    await trim_acknowledged_stream(redis, "stream", "workers")

    redis.xtrim.assert_awaited_once_with(
        "stream",
        minid="7-2",
        approximate=True,
    )


@pytest.mark.asyncio
async def test_trim_failure_never_falls_back_to_maxlen():
    redis = AsyncMock()
    redis.xinfo_groups.return_value = [
        {"name": "workers", "last-delivered-id": "7-2"},
    ]
    redis.xpending.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await trim_acknowledged_stream(redis, "stream", "workers")

    redis.xtrim.assert_not_awaited()


@pytest.mark.asyncio
async def test_trim_all_groups_respects_slowest_pending_consumer():
    redis = AsyncMock()
    redis.xinfo_groups.return_value = [
        {"name": "fast", "last-delivered-id": "20-0"},
        {"name": "slow", "last-delivered-id": "12-0"},
    ]
    redis.xpending.side_effect = [
        {"pending": 0, "min": None},
        {b"pending": 1, b"min": b"4-0"},
    ]

    await trim_acknowledged_stream(redis, "stream")

    redis.xtrim.assert_awaited_once_with(
        "stream",
        minid="4-0",
        approximate=True,
    )
