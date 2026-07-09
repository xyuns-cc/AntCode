"""Crawl 批次状态推导 loop。

F: master 端周期扫描 ``RUNNING`` 状态的 CrawlBatch，把它关联的所有
``TaskRun`` 状态汇总，推导批次终态：
- 所有 run 都 SUCCESS → batch COMPLETED
- 至少一个 FAILED + 无非终态 → batch FAILED
- 全 CANCELLED → batch CANCELLED

设计边界（一期）：
- Leader-gated（与 log_ingest_loop/result_loop 一致的自愈式 gate）。
- 只做状态汇总，不做重派/重试；重试逻辑归 retry_service。
- 靠遍历（TaskRun 用 result_data JSON 关联 batch_id）。批次量级 <1k 时可
  接受；产品化后再考虑加 CrawlBatch.dispatched_run_ids 索引或独立关联表。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loguru import logger

from antcode_core.domain.models import CrawlBatch, TaskRun  # noqa: F401  # TaskRun 用于 raw() 类型标注
from antcode_core.domain.models.enums import BatchStatus, TaskStatus

from antcode_master.leader import ensure_leader

# 终态集合与 execution_status_service 保持一致
_RUN_TERMINAL_STATES = {
    TaskStatus.SUCCESS,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.TIMEOUT,
    TaskStatus.SKIPPED,
    TaskStatus.REJECTED,
}

# T7-B2b: 聚合 SQL 里用字面量字符串（TaskStatus.value）
_TERMINAL_STR = tuple(s.value for s in _RUN_TERMINAL_STATES)
_SUCCESS_STR = TaskStatus.SUCCESS.value
_CANCELLED_STR = TaskStatus.CANCELLED.value
_FAILED_LIKE_STR = (TaskStatus.FAILED.value, TaskStatus.TIMEOUT.value, TaskStatus.REJECTED.value)


class CrawlBatchStatusLoop:
    """周期扫 RUNNING batch，把子任务终态汇总到 batch 终态。"""

    def __init__(self, poll_interval_seconds: float = 30.0) -> None:
        self._poll_interval = poll_interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"crawl 批次状态推导 loop 已启动: interval={self._poll_interval}s"
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("crawl 批次状态推导 loop 已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                if not await ensure_leader():
                    await asyncio.sleep(self._poll_interval)
                    continue
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"crawl 批次状态推导 loop 异常: {exc}")
            await asyncio.sleep(self._poll_interval)

    # R1-P1-16: 空批次/派发失败批次超过此阈值仍无 matched → 标 FAILED，
    # 避免永久 RUNNING 被 alert loop 无限扫。
    EMPTY_BATCH_TIMEOUT_SECONDS = 900  # 15 分钟

    async def _tick(self) -> None:
        # R1-P1-15: PAUSED 批次**不能**在 _reconcile_batch 里推成 COMPLETED，
        # PAUSED 语义是"暂停"、未派发的 seed 应能 RESUME 继续，若推成终态
        # 相当于变相截断。这里只扫 RUNNING，让 PAUSED 留在 PAUSED，等
        # RESUME 后回到 RUNNING 再推。
        batches = await CrawlBatch.filter(status=BatchStatus.RUNNING.value).all()
        if not batches:
            return

        # T7-B2b (P1-5): 单条聚合查询代替 N 次 raw()。原实现 N 个 batch 每
        # tick 发 N 次 SELECT * FROM task_executions WHERE ...=$1，N 大时
        # 30s tick 变成放大器。改成 WHERE ... = ANY($1) GROUP BY，一次拿
        # 所有批次的计数快照，Python 端按 batch_id 分派决策。
        stats = await self._fetch_batch_stats([b.public_id for b in batches])

        for batch in batches:
            try:
                await self._reconcile_batch(batch, stats.get(batch.public_id))
            except Exception as exc:
                logger.exception(
                    f"batch 状态推导失败: batch_id={batch.public_id} err={exc}"
                )

    async def _fetch_batch_stats(
        self, batch_ids: list[str]
    ) -> dict[str, dict[str, int]]:
        """一次拉出所有 batch 的 run 状态计数。

        Returns:
            ``{batch_id: {total, success, failed, cancelled, active}}``
            没有任何 run 的 batch 不会出现在结果里（原 matched=[] 分支照旧
            走空批次超时兜底）。
        """
        if not batch_ids:
            return {}
        from tortoise import connections

        # `= ANY($1)` 让 asyncpg 用数组参数，效率高于 IN 展开
        sql = f"""
            SELECT
                result_data->>'crawl_batch_id' AS batch_id,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = '{_SUCCESS_STR}') AS success,
                COUNT(*) FILTER (WHERE status IN {_FAILED_LIKE_STR}) AS failed,
                COUNT(*) FILTER (WHERE status = '{_CANCELLED_STR}') AS cancelled,
                COUNT(*) FILTER (WHERE status NOT IN {_TERMINAL_STR}) AS active
            FROM task_executions
            WHERE result_data->>'crawl_batch_id' = ANY($1)
            GROUP BY result_data->>'crawl_batch_id'
        """
        conn = connections.get("default")
        _, rows = await conn.execute_query(sql, [batch_ids])
        out: dict[str, dict[str, int]] = {}
        for row in rows:
            bid = row.get("batch_id")
            if not bid:
                continue
            out[bid] = {
                "total": int(row.get("total", 0)),
                "success": int(row.get("success", 0)),
                "failed": int(row.get("failed", 0)),
                "cancelled": int(row.get("cancelled", 0)),
                "active": int(row.get("active", 0)),
            }
        return out

    async def _reconcile_batch(
        self,
        batch: CrawlBatch,
        stat: dict[str, int] | None,
    ) -> None:
        """基于聚合结果推导单个 batch 终态。

        `stat=None` 表示该 batch 目前一条 run 都没有——走空批次超时兜底。
        """
        if not stat:
            # R1-P1-16: 空批次超时兜底 FAILED，避免永久 RUNNING
            if batch.started_at:
                elapsed = (datetime.now(UTC) - batch.started_at).total_seconds()
                if elapsed > self.EMPTY_BATCH_TIMEOUT_SECONDS:
                    batch.status = BatchStatus.FAILED.value
                    batch.completed_at = datetime.now(UTC)
                    await batch.save(update_fields=["status", "completed_at"])
                    logger.warning(
                        f"batch 空转超时 FAILED: batch_id={batch.public_id} "
                        f"elapsed={elapsed:.0f}s"
                    )
            return

        if stat["active"] > 0:
            return  # 还有正在跑的 run

        total = stat["total"]
        success = stat["success"]
        failed = stat["failed"]
        cancelled = stat["cancelled"]

        if success == total:
            new_status = BatchStatus.COMPLETED.value
        elif cancelled == total:
            new_status = BatchStatus.CANCELLED.value
        elif failed > 0 and success + failed + cancelled == total:
            # 部分失败视整体失败（一期最保守，等未来做 partial_success 状态）
            new_status = BatchStatus.FAILED.value
        else:
            new_status = BatchStatus.COMPLETED.value

        batch.status = new_status
        batch.completed_at = datetime.now(UTC)
        await batch.save(update_fields=["status", "completed_at"])
        logger.info(
            f"batch 状态推导: batch_id={batch.public_id} status={new_status} "
            f"total={total} success={success} failed={failed} cancelled={cancelled}"
        )


crawl_batch_status_loop = CrawlBatchStatusLoop()
