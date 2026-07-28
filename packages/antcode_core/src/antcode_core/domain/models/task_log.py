"""任务执行日志模型（``task_logs`` 表）。

P1-01 修复：此前依赖"迁移 37 add_task_logs"手工建表，但 ``migrations/models/``
下只有 README，新部署经 ``scripts/init_db.py`` +
``Tortoise.generate_schemas`` 走 model → DDL 的路线时并没有对应的 ORM
model，导致 ``task_logs`` 表**根本不建**。所有对该表的裸 SQL 读写都会
在新集群启动即失败，日志页面永远空。

本 model 与 ``application/services/logs/postgres_log_service.py`` 里
``INSERT INTO "task_logs" (event_id, run_id, log_type, content, sequence,
timestamp, level, source)`` 及 ``SELECT ...`` 的字段签名严格对齐。

字段来源：
- ``postgres_log_service.PostgresLogEntry`` 的 DTO 定义。
- ``postgres_log_service.append_entries`` / ``list_entries`` / ``count``
  的裸 SQL。
- ``log_cleanup_service._cleanup_postgres_logs``（``timestamp`` 用作
  按期清理列，需要索引）。

注意：``event_id`` 的"部分唯一索引"（``WHERE event_id IS NOT NULL``）
需要与 ``INSERT ... ON CONFLICT ("event_id") WHERE "event_id" IS NOT
NULL DO NOTHING`` 的推断谓词完全匹配。Tortoise ORM 无法声明部分唯一索引，
因此该索引在 ``scripts/init_db.py`` 的 PERFORMANCE_INDEXES 中显式建立，
model 本身只声明 event_id 为普通 nullable 列 + 普通索引。
"""

from __future__ import annotations

from tortoise import fields
from tortoise.indexes import Index
from tortoise.models import Model


class TaskLog(Model):
    """任务执行日志行（一次任务运行会产生 N 行）。"""

    id = fields.BigIntField(primary_key=True)

    # exactly-once 幂等键；上游 LogIngestLoop 走 ON CONFLICT DO NOTHING。
    # 允许 NULL（旧写入路径 / 系统日志可能没有 event_id）。
    event_id = fields.CharField(max_length=128, null=True, description="幂等事件ID")

    # 关联的执行 ID：``TaskRun.run_id``（未建外键，跨服务写入时避免 FK 争用）。
    run_id = fields.CharField(max_length=64, db_index=True, description="执行 run_id")

    # 日志分类：``stdout`` / ``stderr`` / ``system`` 等。
    log_type = fields.CharField(max_length=16, default="stdout", description="日志类型")

    # 日志正文（可能是多行拼接后的整段文本）。
    content = fields.TextField(description="日志正文")

    # 单个 run 内的顺序号，用于稳定分页与前端顺序还原。
    sequence = fields.BigIntField(default=0, description="run 内单调递增序号")

    # 事件发生时间（由上游填入，非 auto_now_add；清理任务按此列过期）。
    timestamp = fields.DatetimeField(db_index=True, description="日志发生时间")

    # 级别：INFO / WARN / ERROR / DEBUG…
    level = fields.CharField(max_length=16, default="INFO", description="日志级别")

    # 来源：worker_report / task_execution / master 等。
    source = fields.CharField(max_length=128, default="task_execution", description="日志来源")

    # 行插入时间（数据库审计视角，与 timestamp 语义不同）。
    created_at = fields.DatetimeField(auto_now_add=True, description="入库时间")

    class Meta:
        table = "task_logs"
        table_description = "任务执行日志"
        # ``list_entries`` 按 (run_id) 过滤，按 (sequence, id) 排序。
        # SSE 历史窗口按数据库写入顺序 (run_id, id) 做稳定 keyset 分页。
        # ``count`` 走 (run_id) 或 (run_id, log_type)。
        # ``_cleanup_postgres_logs`` 走 timestamp（单列索引由 db_index 提供）。
        indexes = [
            ("run_id", "sequence"),
            Index(fields=("run_id", "id"), name="idx_task_logs_run_id_id"),
            ("run_id", "log_type"),
            ("event_id",),
        ]
        ordering = ["sequence", "id"]


__all__ = ["TaskLog"]
