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


def test_the_crawl_delete_fence_no_longer_reaches_the_read_path() -> None:
    """``xreadgroup`` 的 ``active_fence_key`` 与它唯一到达的 ``ensure_active_group``
    已随 Crawl 队列一起删除；补建组只剩 ``ensure_group`` 一条路。留着这条断言是为了
    让"又有人把 fence 参数加回读路径"必须先改这里。"""
    assert "active_fence_key" not in inspect.signature(StreamClient.xreadgroup).parameters
    assert not hasattr(StreamClient, "ensure_active_group")
    assert not hasattr(StreamClient, "xadd_unique")
