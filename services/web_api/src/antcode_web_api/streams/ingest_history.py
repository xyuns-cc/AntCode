"""Bounded two-pass history windows for SSE log replay."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.logs.postgres_log_service import postgres_log_service
from antcode_core.infrastructure.redis import get_redis_client, log_stream_key

from antcode_web_api.streams.ingest_decoder import decode_batch, decode_value
from antcode_web_api.streams.sse import normalize_sequence

# 窗口回放页大小：窗口内的行在计划期已全部通过行数 + 字节预算，可用大页。
HISTORY_PAGE_SIZE = 200
# P1-SSE-02: 计划期"最新历史"扫描的单次物化行数上界。该扫描先物化再记预算，
# 单行 content 上限 1,048,576 字符（多字节 UTF-8 下近 4 MiB），200 行大页一次
# 可物化近 800 MiB；小块取行把预算检查前的敞口压到一小块。
HISTORY_FETCH_CHUNK_ROWS = 25
HistorySizer = Callable[[dict[str, Any]], int]
HistoryCheckpoint = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class PostgresHistoryWindow:
    run_id: str
    snapshot_id: int
    lower: int | None
    sent_lines: int
    total_bytes: int
    truncated: bool


@dataclass(frozen=True)
class _LegacyPosition:
    message_id: str
    entry_index: int


@dataclass(frozen=True)
class LegacyHistoryWindow:
    run_id: str
    snapshot_id: str
    lower: _LegacyPosition | None
    sent_lines: int
    total_bytes: int
    truncated: bool


HistoryWindow = PostgresHistoryWindow | LegacyHistoryWindow


@dataclass
class _HistoryBudget:
    max_lines: int
    max_bytes: int
    sent_lines: int = 0
    total_bytes: int = 0
    truncated: bool = False

    def accept(self, size: int) -> bool:
        if self.sent_lines >= self.max_lines or self.total_bytes + size > self.max_bytes:
            self.truncated = True
            return False
        self.sent_lines += 1
        self.total_bytes += size
        return True


class IngestLogHistoryReader:
    """Select newest history, then stream that stable window oldest-first."""

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace

    async def plan_latest(
        self,
        run_id: str,
        *,
        max_lines: int,
        max_bytes: int,
        size_of: HistorySizer,
        checkpoint: HistoryCheckpoint,
    ) -> HistoryWindow:
        pg_window = await self._plan_postgres(
            run_id, max_lines=max_lines, max_bytes=max_bytes, size_of=size_of, checkpoint=checkpoint
        )
        if pg_window is not None:
            return pg_window
        return await self._plan_legacy(
            run_id, max_lines=max_lines, max_bytes=max_bytes, size_of=size_of, checkpoint=checkpoint
        )

    async def iter_window(
        self, window: HistoryWindow, *, checkpoint: HistoryCheckpoint
    ) -> AsyncIterator[dict[str, Any]]:
        if isinstance(window, PostgresHistoryWindow):
            async for entry in self._iter_postgres_window(window, checkpoint):
                yield entry
            return
        async for entry in self._iter_legacy_window(window, checkpoint):
            yield entry

    async def _plan_postgres(
        self,
        run_id: str,
        *,
        max_lines: int,
        max_bytes: int,
        size_of: HistorySizer,
        checkpoint: HistoryCheckpoint,
    ) -> PostgresHistoryWindow | None:
        snapshot_id = await postgres_log_service.latest_snapshot_id(run_id)
        if snapshot_id <= 0:
            return None
        budget = _HistoryBudget(max_lines, max_bytes)
        lower: int | None = None
        saw_entry = False
        async with aclosing(self._iter_postgres_latest(run_id, snapshot_id)) as latest:
            async for entry in latest:
                await checkpoint()
                saw_entry = True
                normalized = _normalize_pg_entry(entry)
                if not budget.accept(size_of(normalized)):
                    break
                lower = entry.storage_id
        if not saw_entry:
            return None
        return PostgresHistoryWindow(
            run_id, snapshot_id, lower, budget.sent_lines, budget.total_bytes, budget.truncated
        )

    async def _plan_legacy(
        self,
        run_id: str,
        *,
        max_lines: int,
        max_bytes: int,
        size_of: HistorySizer,
        checkpoint: HistoryCheckpoint,
    ) -> LegacyHistoryWindow:
        redis = await self._redis_client()
        stream_key = log_stream_key(run_id, namespace=self._namespace)
        snapshot_id = await self._legacy_snapshot_id(redis, stream_key)
        budget = _HistoryBudget(max_lines, max_bytes)
        lower: _LegacyPosition | None = None
        latest = self._iter_legacy_latest(
            redis,
            stream_key=stream_key,
            snapshot_id=snapshot_id,
            run_id=run_id,
        )
        async with aclosing(latest):
            async for position, entry in latest:
                await checkpoint()
                if not budget.accept(size_of(entry)):
                    break
                lower = position
        return LegacyHistoryWindow(run_id, snapshot_id, lower, budget.sent_lines, budget.total_bytes, budget.truncated)

    async def _iter_postgres_latest(self, run_id: str, snapshot_id: int) -> AsyncGenerator[Any, None]:
        before: int | None = None
        while True:
            page = await postgres_log_service.list_latest_page(
                run_id,
                limit=HISTORY_FETCH_CHUNK_ROWS,
                snapshot_id=snapshot_id,
                before=before,
            )
            if not page:
                return
            for entry in page:
                yield entry
            if len(page) < HISTORY_FETCH_CHUNK_ROWS:
                return
            oldest = page[-1]
            before = oldest.storage_id

    async def _iter_postgres_window(
        self,
        window: PostgresHistoryWindow,
        checkpoint: HistoryCheckpoint,
    ) -> AsyncIterator[dict[str, Any]]:
        if window.lower is None:
            return
        after: int | None = None
        emitted = 0
        while emitted < window.sent_lines:
            page = await postgres_log_service.list_history_window_page(
                window.run_id,
                limit=min(HISTORY_PAGE_SIZE, window.sent_lines - emitted),
                snapshot_id=window.snapshot_id,
                lower=window.lower,
                after=after,
            )
            if not page:
                break
            for entry in page:
                await checkpoint()
                yield _normalize_pg_entry(entry)
                emitted += 1
            newest = page[-1]
            after = newest.storage_id
        self._verify_window_count(window.sent_lines, emitted)

    async def _iter_legacy_latest(
        self,
        redis: Any,
        *,
        stream_key: str,
        snapshot_id: str,
        run_id: str,
    ) -> AsyncGenerator[tuple[_LegacyPosition, dict[str, Any]], None]:
        max_id = snapshot_id
        while max_id:  # P1-SSE-02: 与 PG 计划扫描同理，预算检查前的物化以小块为界
            messages = await redis.xrevrange(stream_key, max=max_id, min="-", count=HISTORY_FETCH_CHUNK_ROWS)
            if not messages:
                return
            for raw_id, fields in messages:
                message_id = decode_value(raw_id)
                entries = decode_batch(fields, run_id_filter=run_id)
                for index in range(len(entries) - 1, -1, -1):
                    entries[index]["source"] = "legacy_history"
                    yield _LegacyPosition(message_id, index), entries[index]
            if len(messages) < HISTORY_FETCH_CHUNK_ROWS:
                return
            max_id = f"({decode_value(messages[-1][0])}"

    async def _iter_legacy_window(
        self,
        window: LegacyHistoryWindow,
        checkpoint: HistoryCheckpoint,
    ) -> AsyncIterator[dict[str, Any]]:
        if window.lower is None:
            return
        redis = await self._redis_client()
        stream_key = log_stream_key(window.run_id, namespace=self._namespace)
        min_id = window.lower.message_id
        emitted = 0
        while emitted < window.sent_lines:
            messages = await redis.xrange(stream_key, min=min_id, max=window.snapshot_id, count=HISTORY_PAGE_SIZE)
            if not messages:
                break
            for raw_id, fields in messages:
                message_id = decode_value(raw_id)
                entries = decode_batch(fields, run_id_filter=window.run_id)
                start = window.lower.entry_index if message_id == window.lower.message_id else 0
                for index in range(start, len(entries)):
                    await checkpoint()
                    entries[index]["source"] = "legacy_history"
                    yield entries[index]
                    emitted += 1
                    if emitted >= window.sent_lines:
                        return
            min_id = f"({decode_value(messages[-1][0])}"
        self._verify_window_count(window.sent_lines, emitted)

    @staticmethod
    async def _legacy_snapshot_id(redis: Any, stream_key: str) -> str:
        messages = await redis.xrevrange(stream_key, max="+", min="-", count=1)
        return decode_value(messages[0][0]) if messages else ""

    @staticmethod
    def _verify_window_count(expected: int, actual: int) -> None:
        if actual != expected:
            raise RuntimeError(f"日志历史窗口在回放期间发生变化: expected={expected} actual={actual}")

    @staticmethod
    async def _redis_client() -> Any:
        redis = await get_redis_client()
        if redis is None:
            raise RuntimeError("Redis 客户端不可用，无法读取 legacy 日志历史")
        return redis


def _normalize_pg_entry(entry: Any) -> dict[str, Any]:
    timestamp = entry.timestamp.isoformat() if entry.timestamp else ""
    normalized = {
        "log_type": entry.log_type or "stdout",
        "content": entry.content or "",
        "timestamp": timestamp,
        "sequence": normalize_sequence(entry.sequence),
        "source": "pg_history",
        "storage_id": int(getattr(entry, "storage_id", 0) or 0),
    }
    event_id = getattr(entry, "event_id", None)
    return {**normalized, "event_id": event_id} if event_id else normalized


__all__ = ["HISTORY_FETCH_CHUNK_ROWS", "HISTORY_PAGE_SIZE", "HistoryCheckpoint", "HistorySizer", "HistoryWindow"]
__all__ += ["IngestLogHistoryReader", "LegacyHistoryWindow", "PostgresHistoryWindow"]
