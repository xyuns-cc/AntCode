"""日志批量持久化服务。

为 Master ``LogIngestLoop`` 提供批量 ``append_entries`` API，避免上游
逐条调用 ``task_log_service.write_log`` 导致的 I/O 风暴。

schema 与 migration 37 (``add_task_logs``) 对齐：PostgreSQL 方言，
``event_id`` 唯一（deduped exactly-once）。表创建以 migration 为准，
本服务只做 CRUD，不再懒加载 DDL。

- **签名稳定**：``append_entries(entries: Sequence[PostgresLogEntry])``。
- **失败可重试**：函数级失败抛 ``Exception``，上游 ingest loop 不 ACK。
- **exactly-once**：``event_id`` 走 ``INSERT ... ON CONFLICT DO NOTHING``，
  重放（reclaim）时不重复入库。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger


@dataclass
class PostgresLogEntry:
    """日志条目 DTO（对齐 ``task_logs`` 表 schema）。"""

    run_id: str
    log_type: str
    content: str
    timestamp: datetime | None = None
    sequence: int = 0
    worker_id: str = ""
    level: str = ""
    source: str = ""
    event_id: str | None = None  # 走 ON CONFLICT 幂等键；None 时不去重


class PostgresLogService:
    """批量日志写入 / 查询服务（PostgreSQL）。

    表 schema 由 ``migrations/models/37_...add_task_logs.py`` 建立，
    本服务假定表已存在（启动流程走 ``db_migrate`` upgrade 分支）。
    """

    async def append_entries(self, entries: Sequence[PostgresLogEntry]) -> int:
        """批量写入日志条目，返回本次尝试写入的行数（含重复被 ON CONFLICT 忽略）。"""
        if not entries:
            return 0

        rows: list[tuple[Any, ...]] = []
        for entry in entries:
            if not entry.run_id:
                continue
            ts = entry.timestamp or datetime.now(tz=UTC)
            rows.append(
                (
                    entry.event_id,
                    entry.run_id,
                    entry.log_type or "stdout",
                    entry.content or "",
                    int(entry.sequence or 0),
                    ts,
                    entry.level or "INFO",
                    entry.source or entry.worker_id or "task_execution",
                )
            )
        if not rows:
            return 0

        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        # P15: task_logs 上是**部分唯一索引** ``idx_task_logs_event_id_unique
        # WHERE event_id IS NOT NULL``（迁移 27_add_audit_log_indexes 加的）。
        # PG 的 ``ON CONFLICT (column)`` 推断要求索引断言与 INSERT 的行
        # 谓词完全匹配，否则报 ``InvalidColumnReferenceError: no unique or
        # exclusion constraint matching``。加显式 WHERE 让推断命中部分索引。
        # 此前每一批日志都 raise → 消息进 pending 反复 reclaim → task_logs
        # 表持续为空 → UI 日志页永远无历史内容。
        sql = (
            'INSERT INTO "task_logs" '
            '("event_id", "run_id", "log_type", "content", "sequence", "timestamp", "level", "source") '
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            'ON CONFLICT ("event_id") WHERE "event_id" IS NOT NULL DO NOTHING'
        )
        # asyncpg-based Tortoise 走 execute_many，失败会抛异常上抛
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

        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        if log_type:
            sql = (
                'SELECT "event_id", "run_id", "log_type", "content", "sequence", '
                '       "timestamp", "level", "source" '
                'FROM "task_logs" '
                'WHERE "run_id" = $1 AND "log_type" = $2 '
                'ORDER BY "sequence" ASC, "id" ASC '
                "LIMIT $3 OFFSET $4"
            )
            params: list[Any] = [run_id, log_type, int(limit), int(offset)]
        else:
            sql = (
                'SELECT "event_id", "run_id", "log_type", "content", "sequence", '
                '       "timestamp", "level", "source" '
                'FROM "task_logs" '
                'WHERE "run_id" = $1 '
                'ORDER BY "sequence" ASC, "id" ASC '
                "LIMIT $2 OFFSET $3"
            )
            params = [run_id, int(limit), int(offset)]

        try:
            _, rows = await conn.execute_query(sql, params)
        except Exception as exc:
            logger.debug("读取 task_logs 失败 run_id={}: {}", run_id, exc)
            return []

        return [
            PostgresLogEntry(
                run_id=row.get("run_id") or "",
                log_type=row.get("log_type") or "stdout",
                content=row.get("content") or "",
                timestamp=row.get("timestamp"),
                sequence=int(row.get("sequence") or 0),
                level=row.get("level") or "",
                source=row.get("source") or "",
                event_id=row.get("event_id"),
            )
            for row in rows or []
        ]

    async def count(
        self,
        run_id: str,
        log_type: str | None = None,
    ) -> int:
        """统计 ``run_id`` 的日志条目总数。"""
        if not run_id:
            return 0

        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        if log_type:
            sql = 'SELECT COUNT(*) AS cnt FROM "task_logs" WHERE "run_id" = $1 AND "log_type" = $2'
            params: list[Any] = [run_id, log_type]
        else:
            sql = 'SELECT COUNT(*) AS cnt FROM "task_logs" WHERE "run_id" = $1'
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
