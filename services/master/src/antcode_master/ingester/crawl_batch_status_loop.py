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

from antcode_core.application.services.crawl.batch_dispatcher_service import (
    crawl_batch_dispatcher_service,
)
from antcode_core.domain.models import CrawlBatch
from antcode_core.domain.models.enums import BatchStatus
from loguru import logger

from antcode_master.ingester.crawl_batch_alerts import crawl_batch_alerts
from antcode_master.ingester.crawl_batch_stats_query import fetch_batch_stats
from antcode_master.leader import ensure_leader


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
        logger.info(f"crawl 批次状态推导 loop 已启动: interval={self._poll_interval}s")

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

    # 一条 run 都没建起来的批次超时兜底 FAILED，避免永久 RUNNING。
    EMPTY_BATCH_TIMEOUT_SECONDS = 900  # 15 分钟

    # 部分派发的批次要留出继续追派的窗口，超时才 FAILED：否则 seed=100 只派到
    # 60 时，60 个全 SUCCESS 就会被推成 COMPLETED，剩下 40 个永久丢失。
    INCOMPLETE_DISPATCH_TIMEOUT_SECONDS = 1800  # 30 分钟

    async def _tick(self) -> None:
        # 只扫 RUNNING：PAUSED 的未派发 seed 应能 RESUME 继续，把它推成终态
        # 等于变相截断。
        batches = await CrawlBatch.filter(status=BatchStatus.RUNNING.value).all()
        if not batches:
            return

        # 单条聚合查询代替 N 次 raw()——每 tick 发 N 条 SELECT，N 大时 30s tick
        # 就是个放大器。
        stats = await fetch_batch_stats([b.public_id for b in batches])
        crawl_batch_alerts.retain({b.public_id for b in batches})
        # R2 seam-5: 进度 hash 以 project **public_id** 为 key（与
        # batch_service.start_batch 的 init_progress 对齐），一次批量解析。
        project_public_ids = await self._fetch_project_public_ids([b.project_id for b in batches])

        for batch in batches:
            try:
                await self._reconcile_batch(
                    batch,
                    stats.get(batch.public_id),
                    project_public_ids.get(batch.project_id),
                )
            except Exception as exc:
                logger.exception(f"batch 状态推导失败: batch_id={batch.public_id} err={exc}")

    @staticmethod
    async def _fetch_project_public_ids(project_ids: list[int]) -> dict[int, str]:
        """批量解析 project 内部 ID → 公开 ID（进度 key 的组成部分）。"""
        unique_ids = list({pid for pid in project_ids if pid})
        if not unique_ids:
            return {}
        from antcode_core.domain.models import Project

        rows = await Project.filter(id__in=unique_ids).only("id", "public_id").all()
        return {row.id: row.public_id for row in rows}

    async def _reconcile_batch(
        self,
        batch: CrawlBatch,
        stat: dict[str, int] | None,
        project_public_id: str | None = None,
    ) -> None:
        """基于聚合结果推导单个 batch 终态。

        `stat=None` 表示该 batch 目前一条 run 都没有——走空批次超时兜底。

        终态写入一律走"仅当仍是 RUNNING 才生效"的条件 UPDATE：loop 手上是
        _tick 时的旧对象，裸 ``save()`` 会把 API 期间改出的 PAUSED / CANCELLED
        刷回 COMPLETED / FAILED，把用户操作静默吞掉。
        """
        seed_count = len(batch.seed_urls or [])

        # 派发链路不做进度加法，进度 hash 会一直停在 init 值。本 loop 已持有
        # run 状态聚合，用它绝对值覆写（幂等）。失败不阻断状态推导——进度是
        # 展示面，状态机才是主干。
        if stat and project_public_id:
            try:
                await self._sync_progress(batch, stat, project_public_id, seed_count)
            except Exception as exc:
                logger.warning(f"batch 进度同步失败(不影响状态推导): batch_id={batch.public_id} err={exc}")

        if not stat:
            # R1-P1-16: 空批次超时兜底 FAILED，避免永久 RUNNING
            await self._fail_after_timeout(
                batch,
                self.EMPTY_BATCH_TIMEOUT_SECONDS,
                f"空转 seed_count={seed_count}",
            )
            return

        # 停滞判定沿用本模块对"不推进的批次"的既有耐心上限，不另立阈值。
        await crawl_batch_alerts.observe_progress(batch, stat, stall_after=self.INCOMPLETE_DISPATCH_TIMEOUT_SECONDS)

        # 批次并发额度只放行一部分 seed，剩下的要靠这里持续追派；不能等
        # active 归零再派，否则一个慢 seed 就把整批堵到派发超时。
        if stat["total"] < seed_count:
            await self._continue_dispatch(batch)

        if stat["active"] > 0:
            return  # 还有正在跑的 run

        total = stat["total"]
        success = stat["success"]
        failed = stat["failed"]
        cancelled = stat["cancelled"]

        # 现有 run 数 < seed 数说明还有 URL 从未派发出去，只看现有 run 的终态
        # 占比就落 COMPLETED 会把它们永久截断。
        if seed_count > 0 and total < seed_count:
            if await self._fail_after_timeout(
                batch,
                self.INCOMPLETE_DISPATCH_TIMEOUT_SECONDS,
                f"seed 未派完 total={total} seed={seed_count}",
            ):
                return
            logger.debug(f"batch seed 未派完，等待追派: batch_id={batch.public_id} total={total} seed={seed_count}")
            return

        if success == total:
            new_status = BatchStatus.COMPLETED.value
        elif cancelled == total:
            new_status = BatchStatus.CANCELLED.value
        elif failed > 0 and success + failed + cancelled == total:
            # 部分失败视整体失败（一期最保守，等未来做 partial_success 状态）
            new_status = BatchStatus.FAILED.value
        else:
            new_status = BatchStatus.COMPLETED.value

        if await self._cas_terminate(batch, new_status):
            detail = f"total={total} seed={seed_count} success={success} failed={failed} cancelled={cancelled}"
            logger.info(f"batch 状态推导: batch_id={batch.public_id} status={new_status} {detail}")
            await crawl_batch_alerts.notify_settled(batch, new_status, detail)

    async def _fail_after_timeout(self, batch: CrawlBatch, timeout_seconds: int, detail: str) -> bool:
        """超过窗口仍没推进的 RUNNING 批次落 FAILED。返回是否已判定超时。"""
        if not batch.started_at:
            return False
        elapsed = (datetime.now(UTC) - batch.started_at).total_seconds()
        if elapsed <= timeout_seconds:
            return False
        if await self._cas_terminate(batch, BatchStatus.FAILED.value):
            settled_detail = f"{detail} elapsed={elapsed:.0f}s"
            logger.warning(f"batch 超时 FAILED: batch_id={batch.public_id} {settled_detail}")
            await crawl_batch_alerts.notify_settled(batch, BatchStatus.FAILED.value, settled_detail)
        return True

    @staticmethod
    async def _continue_dispatch(batch: CrawlBatch) -> None:
        """追派批次并发额度上一轮没放行的 seed。

        失败只记日志：INCOMPLETE_DISPATCH_TIMEOUT_SECONDS 才是"永远派不完"的
        兜底，让异常在这里中断推导会把那条兜底一起废掉。
        """
        try:
            await crawl_batch_dispatcher_service.handle_batch_event("batch_resumed", batch.public_id)
        except Exception as exc:
            logger.warning(f"batch 追派失败(不影响状态推导): batch_id={batch.public_id} err={exc}")

    @staticmethod
    async def _sync_progress(
        batch: CrawlBatch,
        stat: dict[str, int],
        project_public_id: str,
        seed_count: int,
    ) -> None:
        """把 run 状态聚合覆写到批次进度 hash（progress 只有 4 个计数位）。

        CANCELLED 归入 failed 侧，好让 total = completed + failed + pending 这条
        恒等式对消费者成立；total 取 max(seed 数, run 数)，重复派发的
        at-least-once 副作用下才不会把 pending 算成负数。
        """
        from antcode_core.application.services.crawl.progress_service import (
            crawl_progress_service,
        )

        completed = stat["success"]
        failed = stat["failed"] + stat["cancelled"]
        total = max(seed_count, stat["total"])
        pending = max(total - completed - failed, 0)
        await crawl_progress_service.sync_progress_counters(
            project_id=project_public_id,
            batch_id=batch.public_id,
            total_urls=total,
            completed_urls=completed,
            failed_urls=failed,
            pending_urls=pending,
            active_workers=stat.get("active_workers", 0),
        )

    async def _cas_terminate(
        self,
        batch: CrawlBatch,
        new_status: str,
    ) -> bool:
        """把 batch 从 RUNNING 条件 UPDATE 到指定终态。

        Returns:
            True  - 更新成功（本 loop 是首个改状态的写者）
            False - 更新失败（API 已抢先把状态改成 PAUSED/CANCELLED/... 或
                    上一轮 loop 已经推过），本 loop 不再动这行数据

        允许的 from-status：``{RUNNING}``。其他状态（PAUSED / CANCELLED /
        COMPLETED / FAILED / PENDING）一律拒绝——PAUSED 走 RESUME 逻辑，
        终态不可回退，PENDING 应由 start_batch 触发。
        """
        now = datetime.now(UTC)
        updated = await CrawlBatch.filter(
            id=batch.id,
            status=BatchStatus.RUNNING.value,
        ).update(status=new_status, completed_at=now)
        if not updated:
            logger.info(
                f"batch {batch.public_id} 状态被 API 抢先改动 (loop 期望 running→{new_status}), 跳过本轮 loop 更新"
            )
            return False
        # 同步内存对象，方便后续 caller 使用（虽然本函数返回后 batch 就不会
        # 再被读了，但保持一致性避免遗漏 bug）
        batch.status = new_status
        batch.completed_at = now
        return True


crawl_batch_status_loop = CrawlBatchStatusLoop()
