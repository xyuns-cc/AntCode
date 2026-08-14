"""Contracts for Crawl-specific operations exposed by StreamClient."""

import inspect
from unittest.mock import AsyncMock

import pytest
from antcode_core.infrastructure.redis.stream_client import StreamClient


@pytest.mark.asyncio
async def test_xadd_batch_active_preserves_fenced_lua_arguments() -> None:
    redis = AsyncMock()
    redis.eval.return_value = [b"1-0", b"2-0"]
    client = StreamClient(redis)

    result = await client.xadd_batch_active(
        "{p}:stream",
        [{"url": "https://one.test"}, {"depth": 1}],
        deleted_fence_key="{p}:deleted",
    )

    assert result == ["1-0", "2-0"]
    assert redis.eval.await_args.args[1:] == (
        2,
        "{p}:stream",
        "{p}:deleted",
        2,
        1,
        "url",
        "https://one.test",
        1,
        "depth",
        "1",
    )


@pytest.mark.asyncio
async def test_ensure_active_group_preserves_fenced_lua_arguments() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 0

    assert (
        await StreamClient(redis).ensure_active_group(
            "{p}:stream",
            "group",
            deleted_fence_key="{p}:deleted",
        )
        is True
    )
    assert redis.eval.await_args.args[1:] == (
        2,
        "{p}:stream",
        "{p}:deleted",
        "group",
    )


def test_xreadgroup_active_fence_is_keyword_only() -> None:
    parameter = inspect.signature(StreamClient.xreadgroup).parameters["active_fence_key"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
