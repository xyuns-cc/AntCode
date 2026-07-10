"""日志批量持久化服务。

为 Master ``LogIngestLoop`` 提供批量 ``append_entries`` API，避免上游
逐条调用 ``task_log_service.write_log`` 导致的 I/O 风暴。

schema 由 ORM model ``antcode_core.domain.models.task_log.TaskLog`` 定义，
新部署走 ``scripts/init_db.py`` 的 ``Tortoise.generate_schemas`` 建表；
``event_id`` 的部分唯一索引（``WHERE event_id IS NOT NULL``）由
``PERFORMANCE_INDEXES`` 显式补建。本服务只做 CRUD，不再懒加载 DDL。

- **签名稳定**：``append_entries(entries: Sequence[PostgresLogEntry])``。
- **失败可重试**：所有 CRUD 失败一律显式抛 ``Exception``，绝不吞异常返
  空列表 / 0 —— DB 故障必须冒泡，让 UI 报错 / 上游不 ACK，而不是伪装成
  "无历史日志"（历史上的 P1-01 就是被 debug + return [] 掩盖了半年）。
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

    表 schema 由 ORM model ``antcode_core.domain.models.task_log.TaskLog``
    定义，``scripts/init_db.py`` 的 ``Tortoise.generate_schemas(safe=True)``
    在首次启动时把表 + 常规索引建齐，随后 ``PERFORMANCE_INDEXES`` 补上
    ``event_id`` 的部分唯一索引。本服务只做 CRUD。
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

        # 不吞异常：DB 故障必须显式冒泡，绝不能返 [] 伪装成"没有历史日志"。
        # 之前 debug + return [] 让 task_logs 表不存在 / 权限缺失 / 连接池打满
        # 全部退化成 UI 空白，用户完全无感知。上游（web_api 路由 /
        # redis_log_stream_service）会把异常转成 5xx 或结构化错误。
        try:
            _, rows = await conn.execute_query(sql, params)
        except Exception:
            logger.exception("读取 task_logs 失败 run_id={}", run_id)
            raise

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

        # 同 list_entries：DB 异常直接抛，绝不返 0 掩盖问题。
        try:
            _, rows = await conn.execute_query(sql, params)
        except Exception:
            logger.exception("统计 task_logs 失败 run_id={}", run_id)
            raise

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
