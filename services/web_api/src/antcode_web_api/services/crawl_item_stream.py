"""Crawl batch item stream reader."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.keys import RedisKeys

PAGE_SIZE = 1000
FIRST_STREAM_ID = "-"
LAST_STREAM_ID = "+"


class RedisRangeClient(Protocol):
    async def xrange(
        self,
        name: str,
        min: str,
        max: str,
        count: int,
    ) -> Sequence[tuple[Any, Mapping[Any, Any]]]: ...


class QueryConnection(Protocol):
    async def execute_query_dict(self, query: str, values: list[Any]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class CrawlBatchItem:
    sequence: int
    payload: Any
    url: str
    timestamp: str
    run_id: str


class CrawlBatchItemReader:
    def __init__(
        self,
        redis_client: RedisRangeClient,
        db_connection: QueryConnection,
        keys: RedisKeys,
    ) -> None:
        self._redis = redis_client
        self._db = db_connection
        self._keys = keys

    async def iter_items(self, batch_id: str, limit: int) -> AsyncIterator[CrawlBatchItem]:
        remaining = limit
        for run_id in await self._list_run_ids(batch_id):
            async for item in self._iter_run(run_id, remaining):
                yield item
                remaining -= 1
                if remaining == 0:
                    return

    async def _list_run_ids(self, batch_id: str) -> list[str]:
        rows = await self._db.execute_query_dict(
            "SELECT run_id FROM task_executions WHERE result_data->>'crawl_batch_id' = $1 ORDER BY id ASC",
            [batch_id],
        )
        return [str(row["run_id"]) for row in rows]

    async def _iter_run(self, run_id: str, limit: int) -> AsyncIterator[CrawlBatchItem]:
        cursor = FIRST_STREAM_ID
        remaining = limit
        stream_key = self._keys.spider_data_stream(run_id)
        while remaining > 0:
            count = min(PAGE_SIZE, remaining)
            page = await self._redis.xrange(stream_key, min=cursor, max=LAST_STREAM_ID, count=count)
            if not page:
                return
            for entry_id, fields in page:
                yield _decode_entry(entry_id, fields, run_id)
                remaining -= 1
            if len(page) < count:
                return
            cursor = f"({_decode_text(page[-1][0], field='entry_id', run_id=run_id)}"


def _decode_text(value: Any, *, field: str, run_id: str) -> str:
    try:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid crawl stream {field}: run_id={run_id}") from exc


def _field(fields: Mapping[Any, Any], name: str, default: Any) -> Any:
    return fields.get(name.encode()) if name.encode() in fields else fields.get(name, default)


def _decode_entry(entry_id: Any, fields: Mapping[Any, Any], run_id: str) -> CrawlBatchItem:
    entry = _decode_text(entry_id, field="entry_id", run_id=run_id)
    try:
        raw_payload = _decode_text(_field(fields, "data", b"{}"), field="data", run_id=run_id)
        payload = json.loads(raw_payload)
        sequence = int(_decode_text(_field(fields, "sequence", b"0"), field="sequence", run_id=run_id))
        url = _decode_text(_field(fields, "url", b""), field="url", run_id=run_id)
        timestamp = _decode_text(_field(fields, "timestamp", b""), field="timestamp", run_id=run_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid crawl stream entry: run_id={run_id} entry_id={entry}") from exc
    return CrawlBatchItem(sequence, payload, url, timestamp, run_id)


async def iter_batch_items(batch_id: str, limit: int) -> AsyncIterator[CrawlBatchItem]:
    from antcode_core.infrastructure.redis.client import get_redis_client
    from tortoise import Tortoise

    reader = CrawlBatchItemReader(
        cast(RedisRangeClient, await get_redis_client()),
        Tortoise.get_connection("default"),
        RedisKeys(settings.REDIS_NAMESPACE),
    )
    async for item in reader.iter_items(batch_id, limit):
        yield item


__all__ = ["CrawlBatchItem", "CrawlBatchItemReader", "iter_batch_items"]
