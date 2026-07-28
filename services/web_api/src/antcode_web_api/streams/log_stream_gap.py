"""Bounded PostgreSQL catch-up reader for live SSE log connections."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from antcode_web_api.streams.sse import normalize_sequence

# P1-SSE-02: 单次 DB 查询的物化行数上界。HTTP 单行 content 上限是
# 1,048,576 字符（多字节 UTF-8 下接近 4 MiB），大页一次物化可接近
# 800 MiB/连接；小块取行让"物化后才算预算"的敞口只有一小块。
GAP_FETCH_CHUNK_ROWS = 25
# P1-SSE-04: 单轮缺口扫描的行数预算。follower 长时间落后时缺口可能等于
# 全量历史；超预算的缺口按 keyset 游标分多轮推进（active 层在 backlog
# 未清空时先排空 broker 队列，再立即安排下一轮扫描）。
GAP_MAX_LINES_PER_PASS = 2000
# P1-SSE-02: 单轮缺口扫描累计字节预算,与行数预算共同封顶单轮读放大;
# 超预算即截断,剩余缺口由下一轮继续。
# P1-round6 5.3: 预算按 SSE 帧真实编码字节计算(recovery_frame_size),不是
# 只算 content(转义后帧可远大于 content: JSON 编码 + SSE prefix + newline)。
GAP_MAX_BYTES_PER_PASS = 4 * 1024 * 1024


@dataclass(frozen=True)
class GapWindow:
    run_id: str
    after_id: int
    snapshot_id: int


@dataclass
class GapScanProgress:
    """单轮缺口扫描的可变进度；迭代结束后由调用方读取截断标志与末行游标。"""

    last_id: int = 0
    truncated: bool = False


@dataclass
class _GapPassBudget:
    """单轮行数 + SSE 帧字节双预算(P1-SSE-02/04, P1-round6 5.3)。

    P1-round6 5.3: 从只算 content bytes 收紧为算 SSE 帧真实编码字节
    (JSON + SSE prefix + newline), 转义后的实际帧可比 raw content 大数倍,
    只算 content 会低估读放大 → 单轮实际 yield 出去的字节远超 4 MiB。
    """

    rows: int = 0
    frame_bytes: int = 0

    def would_exceed(self, frame_size: int) -> bool:
        """预检下一帧；首帧可独占本轮，保证 keyset 游标始终向前推进。

        单条日志在写入边界已有独立上限。若首帧编码后大于本轮累计预算，
        仍发送该帧并在下一帧前截断；否则永远无法越过这一 storage_id。
        """
        if self.rows == 0:
            return False
        return (self.rows + 1 > GAP_MAX_LINES_PER_PASS) or (self.frame_bytes + frame_size > GAP_MAX_BYTES_PER_PASS)

    def record(self, frame_size: int) -> None:
        """在 yield 前登记本行帧字节, 与 would_exceed 配对使用。"""
        self.rows += 1
        self.frame_bytes += frame_size


class GapWindowError(RuntimeError):
    """The gap keyset scan returned out-of-order or out-of-range rows."""


class LogGapReader(Protocol):
    async def plan(self, run_id: str, after_id: int) -> GapWindow: ...

    def iter_window(
        self,
        window: GapWindow,
        progress: GapScanProgress,
    ) -> AsyncGenerator[dict[str, Any], None]: ...


class PostgresLogGapReader:
    """Keyset-scan rows inserted after a connection watermark up to a fixed snapshot."""

    async def plan(self, run_id: str, after_id: int) -> GapWindow:
        if not run_id or after_id < 0:
            raise ValueError("PG 日志缺口游标无效")
        connection = _connection()
        _, rows = await connection.execute_query(
            'SELECT COALESCE(MAX("id"), 0) AS "max_id" FROM "task_logs" WHERE "run_id" = $1',
            [run_id],
        )
        snapshot_id = int(rows[0].get("max_id") or 0) if rows else 0
        return GapWindow(run_id, after_id, snapshot_id)

    async def iter_window(
        self,
        window: GapWindow,
        progress: GapScanProgress,
    ) -> AsyncGenerator[dict[str, Any], None]:
        # P1-SSE-04: 纯 keyset 分页（WHERE id > cursor ORDER BY id LIMIT n），
        # 不做每轮全量 COUNT / OFFSET 定位，百万行缺口不再接近二次索引扫描。
        # P1-round6 5.3: 预算按 SSE 帧真实字节, 在 yield 前判断 (原 yield 后
        # 判断会让超预算的这一帧已经进入 caller)。
        from antcode_web_api.streams.log_stream_replay import recovery_frame_size

        cursor = window.after_id
        budget = _GapPassBudget()
        while cursor < window.snapshot_id:
            page = await self._fetch_page(window, cursor)
            if not page:
                return
            cursor = self._validate_page(page, cursor, window.snapshot_id)
            for row in page:
                entry = _normalize_row(row)
                frame_size = recovery_frame_size(window.run_id, entry)
                if budget.would_exceed(frame_size):
                    progress.truncated = True
                    return
                progress.last_id = int(row.get("id") or 0)
                budget.record(frame_size)
                yield entry
            if len(page) < GAP_FETCH_CHUNK_ROWS:
                return

    @staticmethod
    async def _fetch_page(window: GapWindow, cursor: int) -> list[dict[str, Any]]:
        columns = '"id", "event_id", "run_id", "log_type", "content", "sequence", "timestamp", "level", "source"'
        sql = (
            f'SELECT {columns} FROM "task_logs" '
            'WHERE "run_id" = $1 AND "id" > $2 AND "id" <= $3 '
            'ORDER BY "id" ASC LIMIT $4'
        )
        _, rows = await _connection().execute_query(
            sql,
            [window.run_id, cursor, window.snapshot_id, GAP_FETCH_CHUNK_ROWS],
        )
        return list(rows or [])

    @staticmethod
    def _validate_page(rows: list[dict[str, Any]], cursor: int, snapshot_id: int) -> int:
        previous = cursor
        for row in rows:
            storage_id = int(row.get("id") or 0)
            if storage_id <= previous or storage_id > snapshot_id:
                raise GapWindowError("PG 日志缺口分页返回了越界或非递增 ID")
            previous = storage_id
        return previous


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    timestamp = row.get("timestamp")
    if timestamp is None:
        timestamp_text = ""
    elif isinstance(timestamp, datetime):
        timestamp_text = timestamp.isoformat()
    elif isinstance(timestamp, str):
        timestamp_text = timestamp
    else:
        raise ValueError(f"PG 日志缺口 timestamp 类型无效: {type(timestamp).__name__}")
    entry = {
        "log_type": row.get("log_type") or "stdout",
        "content": row.get("content") or "",
        "timestamp": timestamp_text,
        "sequence": normalize_sequence(row.get("sequence")),
        "source": "pg_gap",
        "storage_id": int(row.get("id") or 0),
    }
    event_id = row.get("event_id")
    return {**entry, "event_id": event_id} if event_id else entry


def _connection() -> Any:
    from tortoise import Tortoise

    return Tortoise.get_connection("default")


postgres_log_gap_reader = PostgresLogGapReader()

__all__ = [
    "GAP_FETCH_CHUNK_ROWS",
    "GAP_MAX_BYTES_PER_PASS",
    "GAP_MAX_LINES_PER_PASS",
    "GapScanProgress",
    "GapWindow",
    "GapWindowError",
    "LogGapReader",
    "PostgresLogGapReader",
    "postgres_log_gap_reader",
]
