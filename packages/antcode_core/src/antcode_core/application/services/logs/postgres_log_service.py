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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

# P1-SSE-01 提交顺序契约：SSE 游标/缺口/恢复全部把 task_logs.id 当作
# per-run 提交顺序。BIGSERIAL 只保证分配顺序 —— 事务 A 先拿到小 ID 但
# 晚提交时，订阅端游标一旦越过更大的 ID，晚提交的小 ID 行会被实时路径
# 判为已读、恢复查询也永远不再返回（永久漏日志）。
# 修复：写入事务内先按 run 取 pg_advisory_xact_lock（提交自动释放），
# 把同一 run 的插入事务串行化 —— 对任意 run，ID 分配顺序即提交顺序，
# 所有 id-watermark 读路径语义恢复正确。锁粒度是 run，不同 run 之间
# 不互斥；批内多 run 时按 run_id 排序获取锁避免死锁。
# 锁 key 派生 / P1-DB-03 删除侧串行化原语见 task_log_run_guard。
from antcode_core.application.services.logs.task_log_run_guard import (
    RUN_ADVISORY_LOCK_SQL,
    TaskRunGoneError,
    missing_task_run_ids,
    run_commit_lock_key,
)

# task_logs 读路径的统一列清单（单一事实来源），SELECT 字符串与 ORM
# ``.values(*...)`` 均由此派生，各查询模块不得再各自复制。
TASK_LOG_COLUMN_NAMES = (
    "id",
    "event_id",
    "run_id",
    "log_type",
    "content",
    "sequence",
    "timestamp",
    "level",
    "source",
)
TASK_LOG_COLUMNS = ", ".join(f'"{name}"' for name in TASK_LOG_COLUMN_NAMES)


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
    storage_id: int = 0


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

        rows: list[list[Any]] = []
        for entry in entries:
            if not entry.run_id:
                continue
            ts = entry.timestamp or datetime.now(tz=UTC)
            rows.append(
                [
                    entry.event_id,
                    entry.run_id,
                    entry.log_type or "stdout",
                    entry.content or "",
                    int(entry.sequence or 0),
                    ts,
                    entry.level or "INFO",
                    entry.source or entry.worker_id or "task_execution",
                ]
            )
        if not rows:
            return 0

        from tortoise.transactions import in_transaction

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
        run_ids = sorted({str(row[1]) for row in rows})
        async with in_transaction("default") as tx:
            for run_id in run_ids:
                # P1-SSE-01: per-run 提交串行化（见模块头注释）。
                await tx.execute_query(RUN_ADVISORY_LOCK_SQL, [run_commit_lock_key(run_id)])
            # P1-DB-03: 锁内校验 TaskRun 仍存在，与删除路径（task_log_run_guard.
            # purge_task_logs_for_runs 持同一把锁）串行化，杜绝不可达孤儿日志。
            missing = await missing_task_run_ids(tx, run_ids)
            if missing:
                raise TaskRunGoneError(f"TaskRun 已删除，拒绝写入日志: run_ids={missing}")
            # asyncpg-based Tortoise 走 execute_many，失败会抛异常上抛
            await tx.execute_many(sql, rows)
        return len(rows)

    async def list_entries(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        log_type: str | None = None,
        latest: bool = False,
    ) -> list[PostgresLogEntry]:
        """按 ``run_id`` 读取历史日志（返回顺序恒为 sequence 升序）。

        latest=True 时取最新的 ``limit`` 行再按升序返回——超长日志被 limit
        截断时保留尾部而非头部，供实时日志流的历史回放使用（用户关心最新内容）。
        """
        if not run_id:
            return []

        from tortoise import Tortoise

        # 排序方向只来自布尔分支，不含外部输入
        order = 'ORDER BY "sequence" DESC, "id" DESC ' if latest else 'ORDER BY "sequence" ASC, "id" ASC '
        conn = Tortoise.get_connection("default")
        if log_type:
            sql = (
                'SELECT "event_id", "run_id", "log_type", "content", "sequence", '
                '       "timestamp", "level", "source" '
                'FROM "task_logs" '
                'WHERE "run_id" = $1 AND "log_type" = $2 ' + order + "LIMIT $3 OFFSET $4"
            )
            params: list[Any] = [run_id, log_type, int(limit), int(offset)]
        else:
            sql = (
                'SELECT "event_id", "run_id", "log_type", "content", "sequence", '
                '       "timestamp", "level", "source" '
                'FROM "task_logs" '
                'WHERE "run_id" = $1 ' + order + "LIMIT $2 OFFSET $3"
            )
            params = [run_id, int(limit), int(offset)]

        # 不吞异常：DB 故障必须显式冒泡，绝不能返 [] 伪装成"没有历史日志"。
        # 之前 debug + return [] 让 task_logs 表不存在 / 权限缺失 / 连接池打满
        # 全部退化成 UI 空白，用户完全无感知。上游（web_api 路由 /
        # ingest_follower.fetch_history）会把异常转成 5xx 或结构化错误。
        try:
            _, rows = await conn.execute_query(sql, params)
        except Exception:
            logger.exception("读取 task_logs 失败 run_id={}", run_id)
            raise

        entries = [
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
        if latest:
            entries.reverse()
        return entries

    async def max_sequence(self, run_id: str, log_type: str) -> int:
        """(run_id, log_type) 的持久化 sequence 高水位（无记录返回 0）。

        供 HTTP 上报路径的进程内计数器在重启后播种，避免同 run 重新从 1
        编号与 PG 已有高 sequence 冲突（订阅端按 sequence 过滤会误丢新帧）。
        """
        if not run_id:
            return 0

        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        sql = (
            'SELECT COALESCE(MAX("sequence"), 0) AS "max_seq" FROM "task_logs" WHERE "run_id" = $1 AND "log_type" = $2'
        )
        _, rows = await conn.execute_query(sql, [run_id, log_type])
        if not rows:
            return 0
        return int(rows[0].get("max_seq") or 0)

    async def latest_snapshot_id(self, run_id: str) -> int:
        """Capture the immutable upper ID bound for one history replay."""
        if not run_id:
            return 0
        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        _, rows = await conn.execute_query(
            'SELECT COALESCE(MAX("id"), 0) AS "max_id" FROM "task_logs" WHERE "run_id" = $1',
            [run_id],
        )
        return int(rows[0].get("max_id") or 0) if rows else 0

    async def list_latest_page(
        self,
        run_id: str,
        *,
        limit: int,
        snapshot_id: int,
        before: int | None = None,
    ) -> list[PostgresLogEntry]:
        """Read one stable newest-first keyset page within ``snapshot_id``."""
        if not run_id or limit <= 0 or snapshot_id <= 0:
            return []
        from tortoise import Tortoise

        conn = Tortoise.get_connection("default")
        if before is None:
            where = 'WHERE "run_id" = $1 AND "id" <= $2 '
            params: list[Any] = [run_id, snapshot_id, limit]
        else:
            where = 'WHERE "run_id" = $1 AND "id" <= $2 AND "id" < $3 '
            params = [run_id, snapshot_id, before, limit]
        limit_param = len(params)
        sql = f'SELECT {TASK_LOG_COLUMNS} FROM "task_logs" {where}ORDER BY "id" DESC LIMIT ${limit_param}'
        _, rows = await conn.execute_query(sql, params)
        return [row_to_log_entry(row) for row in rows or []]

    async def list_history_window_page(
        self,
        run_id: str,
        *,
        limit: int,
        snapshot_id: int,
        lower: int,
        after: int | None = None,
    ) -> list[PostgresLogEntry]:
        """Read one oldest-first keyset page from a stable latest window."""
        if not run_id or limit <= 0 or snapshot_id <= 0:
            return []
        from antcode_core.application.services.logs.postgres_log_history_query import fetch_history_window_rows

        rows = await fetch_history_window_rows(
            run_id,
            limit=limit,
            snapshot_id=snapshot_id,
            lower=lower,
            after=after,
        )
        return [row_to_log_entry(row) for row in rows or []]

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


def row_to_log_entry(row: dict[str, Any]) -> PostgresLogEntry:
    """``TASK_LOG_COLUMN_NAMES`` 行 → ``PostgresLogEntry`` 的唯一映射实现。

    各查询模块共用本函数；模块级的额外校验（event_id / storage_id 等）
    在映射完成后自行叠加。
    """
    return PostgresLogEntry(
        run_id=row.get("run_id") or "",
        log_type=row.get("log_type") or "stdout",
        content=row.get("content") or "",
        timestamp=row.get("timestamp"),
        sequence=int(row.get("sequence") or 0),
        level=row.get("level") or "",
        source=row.get("source") or "",
        event_id=row.get("event_id"),
        storage_id=int(row.get("id") or 0),
    )


__all__ = [
    "TASK_LOG_COLUMNS",
    "TASK_LOG_COLUMN_NAMES",
    "PostgresLogEntry",
    "PostgresLogService",
    "TaskRunGoneError",
    "postgres_log_service",
    "postgres_task_log_service",
    "row_to_log_entry",
    "run_commit_lock_key",
]
