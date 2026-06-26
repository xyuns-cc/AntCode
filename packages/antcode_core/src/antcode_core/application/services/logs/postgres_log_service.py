"""日志批量持久化服务

为 Master ``LogIngestLoop`` 提供批量 ``append_entries`` API，避免上游
逐条调用 ``task_log_service.write_log`` 导致的 I/O 风暴和异常处理散乱。

P2 改造：从"写盘聚合"策略改为真正落 PG (``task_logs`` 表)。
- 通过 Tortoise raw connection 写库，避免引入新的 ORM Model 注册依赖；
- 表 schema 用 ``CREATE TABLE IF NOT EXISTS`` 在首次写入时懒加载；
- ``append_entries`` 一次 ``executemany``，端到端零幽灵成功；
- ``list_entries`` / ``count`` 给 ``task_log_service`` 走 PG 历史查询，
  也给 ``web_api`` WebSocket 历史回放使用。

设计目标：
- **签名稳定**：``append_entries(entries: Sequence[PostgresLogEntry])``。
- **失败可重试**：函数级失败抛 ``Exception``，上游 ingest loop 不 ACK，
  Redis 消息保留在 pending，靠 ``XAUTOCLAIM`` 重试。
- **字段对齐**：增加 ``level`` / ``source`` / ``extra_data`` 字段，跟
  Worker ``LogEntry`` 的元数据完全对齐。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `task_logs` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `run_id` VARCHAR(64) NOT NULL,
    `log_type` VARCHAR(16) NOT NULL DEFAULT 'stdout',
    `content` MEDIUMTEXT NOT NULL,
    `timestamp` DATETIME(6) NOT NULL,
    `sequence` BIGINT NOT NULL DEFAULT 0,
    `level` VARCHAR(16) NOT NULL DEFAULT '',
    `source` VARCHAR(128) NOT NULL DEFAULT '',
    `extra_data` JSON NULL,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_task_logs_run_id` (`run_id`),
    KEY `idx_task_logs_run_log_type` (`run_id`, `log_type`),
    KEY `idx_task_logs_run_seq` (`run_id`, `sequence`)
) CHARACTER SET utf8mb4 COMMENT='任务运行日志条目';
"""


@dataclass
class PostgresLogEntry:
    """日志条目 DTO

    字段命名沿用 ``task_logs`` 表 schema。``level`` / ``source`` /
    ``extra_data`` 留给 Worker ``LogEntry`` 的扩展元数据,允许默认值,
    避免上游构造 TypeError。
    """

    run_id: str
    log_type: str
    content: str
    timestamp: datetime | None = None
    sequence: int = 0
    worker_id: str = ""
    level: str = ""
    source: str = ""
    extra_data: dict[str, Any] = field(default_factory=dict)


class PostgresLogService:
    """批量日志写入服务（PG 持久化）

    用 ``append_entries`` 一次写一批；通过 Tortoise raw connection
    走 ``executemany`` 写库。表 schema 在首次写入时懒加载,无需额外
    迁移依赖。
    """

    _table_initialized: bool = False
    _init_lock: asyncio.Lock | None = None

    def __init__(self) -> None:
        self._init_lock = None

    def _ensure_lock(self) -> asyncio.Lock:
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        return self._init_lock

    async def _ensure_table(self) -> None:
        if self._table_initialized:
            return
        async with self._ensure_lock():
            if self._table_initialized:
                return
            from tortoise import Tortoise

            conn = Tortoise.get_connection("default")
            await conn.execute_script(_CREATE_TABLE_SQL)
            self._table_initialized = True

    async def append_entries(self, entries: Sequence[PostgresLogEntry]) -> int:
        """批量写入日志条目，返回成功写入的条数。

        - 任何 IO 失败都会抛回上游（用于保留消息 pending → 重试）。
        - 空 ``run_id`` 的条目会被跳过（防御性）。
        """
        if not entries:
            return 0

        rows: list[tuple[Any, ...]] = []
        for entry in entries:
            if not entry.run_id:
                continue
            ts = entry.timestamp or datetime.now(tz=UTC)
            extra_json = json.dumps(entry.extra_data or {}, ensure_ascii=False)
            rows.append(
                (
                    entry.run_id,
                    entry.log_type or "stdout",
                    entry.content or "",
                    ts,
                    int(entry.sequence or 0),
                    entry.level or "",
                    entry.source or entry.worker_id or "",
                    extra_json,
                )
            )

        if not rows:
            return 0

        await self._ensure_table()

        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        sql = (
            "INSERT INTO `task_logs` "
            "(`run_id`, `log_type`, `content`, `timestamp`, `sequence`, "
            " `level`, `source`, `extra_data`) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        # Tortoise execute_many 走底层 driver，失败会抛异常上抛
        await conn.execute_many(sql, rows)
        return len(rows)

    async def list_entries(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        log_type: str | None = None,
    ) -> list[PostgresLogEntry]:
        """按 ``run_id`` 读取历史日志（按 sequence/timestamp 升序）。"""
        if not run_id:
            return []

        await self._ensure_table()

        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        if log_type:
            sql = (
                "SELECT `run_id`, `log_type`, `content`, `timestamp`, "
                "       `sequence`, `level`, `source`, `extra_data` "
                "FROM `task_logs` "
                "WHERE `run_id` = %s AND `log_type` = %s "
                "ORDER BY `sequence` ASC, `id` ASC "
                "LIMIT %s OFFSET %s"
            )
            params: list[Any] = [run_id, log_type, int(limit), int(offset)]
        else:
            sql = (
                "SELECT `run_id`, `log_type`, `content`, `timestamp`, "
                "       `sequence`, `level`, `source`, `extra_data` "
                "FROM `task_logs` "
                "WHERE `run_id` = %s "
                "ORDER BY `sequence` ASC, `id` ASC "
                "LIMIT %s OFFSET %s"
            )
            params = [run_id, int(limit), int(offset)]

        try:
            _, rows = await conn.execute_query(sql, params)
        except Exception as exc:
            logger.debug("读取 task_logs 失败 run_id={}: {}", run_id, exc)
            return []

        result: list[PostgresLogEntry] = []
        for row in rows or []:
            extra_raw = row.get("extra_data")
            extra: dict[str, Any] = {}
            if isinstance(extra_raw, str) and extra_raw:
                try:
                    extra = json.loads(extra_raw)
                except Exception:
                    extra = {}
            elif isinstance(extra_raw, dict):
                extra = extra_raw

            result.append(
                PostgresLogEntry(
                    run_id=row.get("run_id") or "",
                    log_type=row.get("log_type") or "stdout",
                    content=row.get("content") or "",
                    timestamp=row.get("timestamp"),
                    sequence=int(row.get("sequence") or 0),
                    level=row.get("level") or "",
                    source=row.get("source") or "",
                    extra_data=extra,
                )
            )
        return result

    async def count(
        self,
        run_id: str,
        log_type: str | None = None,
    ) -> int:
        """统计 ``run_id`` 的日志条目总数。"""
        if not run_id:
            return 0

        await self._ensure_table()

        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        if log_type:
            sql = "SELECT COUNT(*) AS cnt FROM `task_logs` WHERE `run_id` = %s AND `log_type` = %s"
            params: list[Any] = [run_id, log_type]
        else:
            sql = "SELECT COUNT(*) AS cnt FROM `task_logs` WHERE `run_id` = %s"
            params = [run_id]

        try:
            _, rows = await conn.execute_query(sql, params)
        except Exception as exc:
            logger.debug("统计 task_logs 失败 run_id={}: {}", run_id, exc)
            return 0

        if not rows:
            return 0
        return int(rows[0].get("cnt") or 0)


postgres_log_service = PostgresLogService()
# 兼容别名 — 旧 import path: ``from postgres_log_service import postgres_task_log_service``
postgres_task_log_service = postgres_log_service


__all__ = [
    "PostgresLogEntry",
    "PostgresLogService",
    "postgres_log_service",
    "postgres_task_log_service",
]
