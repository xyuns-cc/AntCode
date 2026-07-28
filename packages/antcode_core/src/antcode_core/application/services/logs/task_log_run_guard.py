"""P1-SSE-01 / P1-DB-03: task_logs 写入与删除的 run 级串行化原语。

- ``run_commit_lock_key``：run 级 pg advisory xact lock key（P1-SSE-01 提交
  顺序契约的锁），在 Python 侧派生，保证所有写入方与删除方算出同一把锁。
- ``TaskRunGoneError`` + ``missing_task_run_ids``：日志写入事务在锁内校验
  TaskRun 仍存在，run 已被删除时明确拒绝（P1-DB-03，不产生孤儿日志）。
- ``purge_task_logs_for_runs``：删除路径在 TaskRun 删除事务**提交后**按批
  取同一把锁清扫竞态窗口内提交的日志残留；批内 run_id 排序取锁与写入侧
  ``sorted(run_ids)`` 锁序一致防死锁，每批一个事务保证单事务 advisory
  lock 数与时长有界。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

RUN_ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock($1)"
_RUN_LOCK_SEED = b"antcode:task_logs:"
_LOCK_KEY_BYTES = 8

# 删除路径清扫日志的每事务 run 批量上限 —— advisory xact lock 在提交前不可
# 释放，批量删除的 run 数可能很大，必须按批开事务。与 spider cleanup 的
# 批量粒度保持同量级。
TASK_LOG_PURGE_BATCH_SIZE = 200


class TaskRunGoneError(RuntimeError):
    """P1-DB-03: 目标 TaskRun 已被删除，日志写入被明确拒绝（不静默丢弃）。

    调用方语义：
    - Master LogIngestLoop：异常冒泡 → 消息留在 PEL，重投时
      ``log_ingest_integrity`` 的 TaskRun 存在性校验会把它显式打入 DLQ；
    - web_api HTTP 上报：路由映射为 409 冲突；
    - task_log_service.write_log：异常冒泡，由调用方按写入失败处理。
    """


def run_commit_lock_key(run_id: str) -> int:
    """run 级 advisory lock key：sha256 前 8 字节的有符号 64 位整数。

    在 Python 侧派生（而非 hashtextextended），保证任何写入方（Master
    ingest / web_api HTTP 上报）与删除方算出的 key 一致。
    """
    digest = hashlib.sha256(_RUN_LOCK_SEED + run_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:_LOCK_KEY_BYTES], "big", signed=True)


async def missing_task_run_ids(tx: Any, run_ids: Sequence[str]) -> list[str]:
    """返回 ``run_ids`` 中在 ``task_executions`` 里已不存在的 run_id（有序）。"""
    ids = list(run_ids)
    placeholders = ", ".join(f"${index + 1}" for index in range(len(ids)))
    sql = f'SELECT "run_id" FROM "task_executions" WHERE "run_id" IN ({placeholders})'
    _, found_rows = await tx.execute_query(sql, ids)
    existing = {row.get("run_id") for row in found_rows or []}
    return sorted(set(ids) - existing)


async def delete_run_dependency_rows(conn: Any, run_ids: Sequence[str]) -> None:
    """删除事务内批量清理 run 级无 FK 附属行。

    任何错误必须让外层删除事务整体回滚；竞态窗口内的日志残留由提交后的
    ``purge_task_logs_for_runs`` 持锁清扫。
    """
    from antcode_core.application.services.crawl.spider_storage_cleanup import (
        iter_cleanup_run_batches,
    )

    for run_batch in iter_cleanup_run_batches(list(run_ids)):
        placeholders = ",".join(f"${index + 1}" for index in range(len(run_batch)))
        for table in ("task_logs", "run_source_snapshots", "task_run_lease_generations"):
            await conn.execute_query(
                f'DELETE FROM "{table}" WHERE "run_id" IN ({placeholders})',
                list(run_batch),
            )


async def purge_task_logs_for_runs(run_ids: Sequence[str]) -> int:
    """删除路径的日志清扫，按 run 级 advisory lock 与在途写入串行化。

    必须在 TaskRun 删除事务提交之后调用：此时任何后续 ``append_entries``
    都会在锁内 EXISTS 校验中被拒绝；本清扫再删掉删除窗口内已提交的最后
    残留。返回删除的日志行数。
    """
    normalized = sorted({str(run_id) for run_id in run_ids if run_id})
    removed = 0
    for offset in range(0, len(normalized), TASK_LOG_PURGE_BATCH_SIZE):
        removed += await _purge_batch(normalized[offset : offset + TASK_LOG_PURGE_BATCH_SIZE])
    return removed


async def _purge_batch(run_ids: list[str]) -> int:
    from tortoise.transactions import in_transaction

    async with in_transaction("default") as tx:
        for run_id in run_ids:
            # run_ids 已整体排序 —— 与 append_entries 相同的锁序防死锁
            await tx.execute_query(RUN_ADVISORY_LOCK_SQL, [run_commit_lock_key(run_id)])
        placeholders = ", ".join(f"${index + 1}" for index in range(len(run_ids)))
        deleted, _ = await tx.execute_query(
            f'DELETE FROM "task_logs" WHERE "run_id" IN ({placeholders})',
            list(run_ids),
        )
    return int(deleted or 0)


__all__ = [
    "RUN_ADVISORY_LOCK_SQL",
    "TASK_LOG_PURGE_BATCH_SIZE",
    "TaskRunGoneError",
    "delete_run_dependency_rows",
    "missing_task_run_ids",
    "purge_task_logs_for_runs",
    "run_commit_lock_key",
]
