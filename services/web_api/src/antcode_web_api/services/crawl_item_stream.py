"""Crawl batch item stream reader."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.keys import RedisKeys
from loguru import logger

PAGE_SIZE = 1000
FIRST_STREAM_ID = "-"
LAST_STREAM_ID = "+"

# C2：重复 item_id 只推进游标、不消耗 limit（跨 run 去重，见 _iter_run）。
# sender 恢复整批重发时会出现"全重复 run"——其每条 entry 的 item_id 都已
# 在前序 run 见过。若不设界，这类 run 会为了找一条从不存在的新条目而翻遍
# 整条 Stream（/export 的 limit 可达 10 万，读放大尖峰）。为每个 run 设置
# "空扫预算"：累计被跳过的重复 entry 达到上限即停扫该 run。预算取 limit 的
# 数倍并设绝对下限，保证正常重发前缀（远小于 limit）被完整吸收后，其后的
# 新条目仍照常产出。
DUP_SCAN_BUDGET_MULTIPLIER = 4
MIN_DUP_SCAN_BUDGET = 1000


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
    # P1-15：item_id 用于读端去重。Gateway 侧批处理非事务写，sender 恢复
    # 整批重发时同一 item_id 会在 Stream 里出现多次；读端按 item_id 集合
    # 去重可保证前端看到的 items 幂等（首次出现为准）。空字符串表示历史
    # entry 无 item_id（当作独立 item 不去重）。
    item_id: str = ""


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
        # P1-15：按 item_id 去重（首次出现为准）。跨 run / 跨批次重发都被吸收。
        # 空 item_id 视为独立 item 不参与去重。去重判定下沉到 _iter_run：
        # 重复 entry 不得消耗读取预算，否则整批重发的 run 会挤掉自身后续
        # 未见条目（总量未达 limit 也会静默缺行）。
        seen: set[str] = set()
        remaining = limit
        for run_id in await self._list_run_ids(batch_id):
            if remaining <= 0:
                return
            async for item in self._iter_run(run_id, remaining, seen):
                yield item
                remaining -= 1

    async def _list_run_ids(self, batch_id: str) -> list[str]:
        rows = await self._db.execute_query_dict(
            "SELECT run_id FROM task_executions WHERE result_data->>'crawl_batch_id' = $1 ORDER BY id ASC",
            [batch_id],
        )
        return [str(row["run_id"]) for row in rows]

    async def _iter_run(self, run_id: str, limit: int, seen: set[str]) -> AsyncIterator[CrawlBatchItem]:
        """按游标翻页产出至多 ``limit`` 条未重复 entry，直到流耗尽。

        重复 item_id 只推进游标、不计入预算，保证同 run 后续未见条目
        仍会被读到（P1-15 整批重发场景）。
        """
        cursor = FIRST_STREAM_ID
        remaining = limit
        dup_scan_budget = max(limit * DUP_SCAN_BUDGET_MULTIPLIER, MIN_DUP_SCAN_BUDGET)
        wasted = 0
        stream_key = self._keys.spider_data_stream(run_id)
        while remaining > 0:
            count = min(PAGE_SIZE, remaining)
            page = await self._redis.xrange(stream_key, min=cursor, max=LAST_STREAM_ID, count=count)
            if not page:
                return
            for entry_id, fields in page:
                item = _decode_entry(entry_id, fields, run_id)
                if item.item_id:
                    if item.item_id in seen:
                        wasted += 1
                        if wasted >= dup_scan_budget:
                            # 整批重发的全重复 run：已空扫预算内 entry 仍无新条目，
                            # 停扫避免 O(stream length) 读放大（正确性不受影响，
                            # 全重复 run 本就无新 item 可产出）。
                            logger.debug(
                                "crawl item 去重空扫达预算上限，停扫该 run: run_id={} wasted={} budget={}",
                                run_id,
                                wasted,
                                dup_scan_budget,
                            )
                            return
                        continue
                    seen.add(item.item_id)
                yield item
                remaining -= 1
                if remaining == 0:
                    return
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
        # P1-15：item_id 可选（历史 entry 可能缺该字段）
        item_id = _decode_text(_field(fields, "item_id", b""), field="item_id", run_id=run_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid crawl stream entry: run_id={run_id} entry_id={entry}") from exc
    return CrawlBatchItem(sequence, payload, url, timestamp, run_id, item_id)


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
