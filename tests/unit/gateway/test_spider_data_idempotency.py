"""Gateway SpiderData idempotency and ambiguous-commit tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.spider_write_fence import SpiderWriteIdentity
from antcode_gateway.handlers.spider_data import SpiderDataHandler
from antcode_gateway.handlers.spider_item_writer import IdempotentSpiderItemWriter
from redis.exceptions import NoScriptError

ITEM_WIDTH = 10
SCRIPT_KEY_COUNT = 9


class _Pipeline:
    def hset(self, *args, **kwargs) -> None:
        pass

    def zadd(self, *args, **kwargs) -> None:
        pass

    def expire(self, *args, **kwargs) -> None:
        pass

    def persist(self, *args, **kwargs) -> None:
        pass

    async def execute(self) -> list[Any]:
        return []


class AmbiguousCommitRedis:
    """Minimal script client that loses the first reply after committing."""

    def __init__(self) -> None:
        self.markers: dict[str, str] = {}
        self.entries: list[tuple[Any, ...]] = []
        self.lose_next_reply = True

    async def script_load(self, script: str) -> str:
        assert "HSET" in script
        return "sha"

    async def evalsha(self, *script_args: Any) -> list[int]:
        sha, numkeys, stream, markers, order, tombstone, lease, revoked, owner, index, expiry, *args = script_args
        assert isinstance(sha, str)
        assert numkeys == SCRIPT_KEY_COUNT
        assert stream == "{antcode}:spider:run-1:data"
        assert markers == "{antcode}:spider:run-1:item-ids"
        assert order == "{antcode}:spider:run-1:item-order"
        assert tombstone == "{antcode}:spider:run-1:tombstone"
        assert lease == "{antcode}:lease:data:worker-1"
        assert revoked == "{antcode}:lease:revoked:worker-1"
        assert owner == "{antcode}:run:owner:run-1"
        assert index == "{antcode}:spider:index:project-1"
        assert expiry == "{antcode}:spider:index:expiry:project-1"
        result = self._commit(tuple(args[6:]))
        if self.lose_next_reply:
            self.lose_next_reply = False
            raise ConnectionError("reply lost after commit")
        return result

    def _commit(self, args: tuple[Any, ...]) -> list[int]:
        item_count = int(args[2])
        flat = args[3:]
        inserted = 0
        for offset in range(0, item_count * ITEM_WIDTH, ITEM_WIDTH):
            digest = str(flat[offset])
            item = tuple(flat[offset + 1 : offset + ITEM_WIDTH])
            item_id = str(item[0])
            previous = self.markers.get(item_id)
            if previous is not None and previous != digest:
                raise RuntimeError(f"SPIDER_ITEM_ID_CONFLICT {item_id}")
            if previous is None:
                self.markers[item_id] = digest
                self.entries.append(item)
                inserted += 1
        return [item_count, inserted, item_count - inserted]

    def pipeline(self, *, transaction: bool) -> _Pipeline:
        assert transaction is False
        return _Pipeline()


def _batch(*, title: str = "Example Domain", item_id: str = "item-1") -> data_pb2.SpiderDataBatch:
    return data_pb2.SpiderDataBatch(
        worker_id="worker-1",
        run_id="run-1",
        project_id="project-1",
        lease_id="lease-1",
        items=[
            data_pb2.SpiderDataItem(
                item_id=item_id,
                spider_name="antcode_rule",
                item_type="default",
                data=f'{{"title":"{title}"}}'.encode(),
                url="https://example.com/",
                timestamp="2026-07-16T10:43:40",
                sequence=1,
            )
        ],
    )


@pytest.mark.asyncio
async def test_replay_after_ambiguous_commit_does_not_append_duplicate() -> None:
    redis = AmbiguousCommitRedis()
    handler = SpiderDataHandler(redis_client=redis)

    with pytest.raises(ConnectionError, match="reply lost after commit"):
        await handler.handle_batch(_batch())
    assert await handler.handle_batch(_batch()) == (1, 0)

    assert len(redis.entries) == 1


@pytest.mark.asyncio
async def test_replayed_item_id_with_different_payload_is_rejected() -> None:
    redis = AmbiguousCommitRedis()
    redis.lose_next_reply = False
    handler = SpiderDataHandler(redis_client=redis)

    assert await handler.handle_batch(_batch()) == (1, 0)
    with pytest.raises(RuntimeError, match="SPIDER_ITEM_ID_CONFLICT"):
        await handler.handle_batch(_batch(title="Changed"))

    assert len(redis.entries) == 1


@pytest.mark.asyncio
async def test_noscript_uses_explicit_eval_without_hiding_other_failures() -> None:
    redis = MagicMock()
    redis.script_load = AsyncMock(return_value="sha")
    redis.evalsha = AsyncMock(side_effect=NoScriptError("missing"))
    redis.eval = AsyncMock(return_value=[1, 1, 0])
    writer = IdempotentSpiderItemWriter(redis, stream_max_len=0, ttl_seconds=0)
    handler = SpiderDataHandler(redis_client=redis)
    payload = handler._item_payload(_batch(), _batch().items[0])

    result = await writer.write(
        "{antcode}:spider:run-1:data",
        "{antcode}:spider:run-1:item-ids",
        "{antcode}:spider:run-1:item-order",
        identity=_identity(),
        tombstone_key="{antcode}:spider:run-1:tombstone",
        index_key="{antcode}:spider:index:project-1",
        index_expiry_key="{antcode}:spider:index:expiry:project-1",
        payloads=[payload],
    )

    assert result.inserted == 1
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_noscript_error_is_exposed() -> None:
    redis = MagicMock()
    redis.script_load = AsyncMock(return_value="sha")
    redis.evalsha = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    writer = IdempotentSpiderItemWriter(redis, stream_max_len=0, ttl_seconds=0)
    handler = SpiderDataHandler(redis_client=redis)
    payload = handler._item_payload(_batch(), _batch().items[0])

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await writer.write(
            "{antcode}:spider:run-1:data",
            "{antcode}:spider:run-1:item-ids",
            "{antcode}:spider:run-1:item-order",
            identity=_identity(),
            tombstone_key="{antcode}:spider:run-1:tombstone",
            index_key="{antcode}:spider:index:project-1",
            index_expiry_key="{antcode}:spider:index:expiry:project-1",
            payloads=[payload],
        )
    redis.eval.assert_not_called()


def _identity() -> SpiderWriteIdentity:
    return SpiderWriteIdentity("antcode", "worker-1", "lease-1", "run-1", "project-1")
