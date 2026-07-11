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

from antcode_core.domain.models import CrawlBatch, TaskRun  # noqa: F401  # TaskRun 用于 raw() 类型标注
from antcode_core.domain.models.enums import BatchStatus, TaskStatus
from loguru import logger

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

    # R1-P1-16: 空批次/派发失败批次超过此阈值仍无 matched → 标 FAILED，
    # 避免永久 RUNNING 被 alert loop 无限扫。
    EMPTY_BATCH_TIMEOUT_SECONDS = 900  # 15 分钟

    # P1-14 (审查报告): seed_urls 尚未派完但 loop 就认为"所有 run 终态"要
    # 提前终结批次。给部分派发的批次一个允许继续追派发的窗口，超时才 FAILED。
    # 场景：seed_urls=100 但派发只到 60 个（redispatch pipeline 卡死等），
    # 原实现 total=60 全 SUCCESS 就把批次标 COMPLETED，剩 40 个永远丢。
    INCOMPLETE_DISPATCH_TIMEOUT_SECONDS = 1800  # 30 分钟

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

    async def _fetch_batch_stats(self, batch_ids: list[str]) -> dict[str, dict[str, int]]:
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
        project_public_id: str | None = None,
    ) -> None:
        """基于聚合结果推导单个 batch 终态。

        `stat=None` 表示该 batch 目前一条 run 都没有——走空批次超时兜底。

        **P1-32 修复**：所有终态写入改成"仅当仍是 RUNNING 才生效"的条件
        UPDATE，杜绝下面这条 lost-update 竞态：
        1. loop._tick 加载 RUNNING 批次列表 A
        2. API 把 A 里某个批次改成 PAUSED / CANCELLED（合法状态机迁移）
        3. loop 拿老对象照旧 ``batch.save()``，把 PAUSED / CANCELLED 又刷回
           COMPLETED / FAILED，API 侧的操作被静默吞掉

        CAS 允许的 from-status 集合固定为 ``{RUNNING}``——loop 本来就只处理
        RUNNING 批次，其他任何状态都表示"API 已抢先，loop 让位"。
        """
        # P1-14 (审查报告): seed_urls 完整性检查所需的分母。JSONField 可能
        # 是 None/空 list/list[str]，统一 fallback。
        seed_count = len(batch.seed_urls or [])

        # R2 seam-5 (接缝修复): crawl 链路没有任何环节做进度加法——
        # batch_dispatcher 走 TaskRun 派发，不经过 queue_service，
        # progress_service.update_progress 全链路零调用，进度 hash 永远停在
        # init 值（completed=0 / pending=total）。本 loop 已经持有每个 batch
        # 的 run 状态聚合，把它作为权威值同步进进度 hash（绝对值覆写，幂等）。
        # 失败不阻断状态推导（进度是展示面，状态机是主干）。
        if stat and project_public_id:
            try:
                await self._sync_progress(batch, stat, project_public_id, seed_count)
            except Exception as exc:
                logger.warning(f"batch 进度同步失败(不影响状态推导): batch_id={batch.public_id} err={exc}")

        if not stat:
            # R1-P1-16: 空批次超时兜底 FAILED，避免永久 RUNNING
            if batch.started_at:
                elapsed = (datetime.now(UTC) - batch.started_at).total_seconds()
                if elapsed > self.EMPTY_BATCH_TIMEOUT_SECONDS:
                    if await self._cas_terminate(batch, BatchStatus.FAILED.value):
                        logger.warning(
                            f"batch 空转超时 FAILED: batch_id={batch.public_id} "
                            f"elapsed={elapsed:.0f}s seed_count={seed_count}"
                        )
            return

        if stat["active"] > 0:
            return  # 还有正在跑的 run

        total = stat["total"]
        success = stat["success"]
        failed = stat["failed"]
        cancelled = stat["cancelled"]

        # P1-14: seed 完整性判定——现有 run 数 < seed_urls 数时不能终结批次，
        # 说明还有 URL 从未派发出去。旧实现只看现有 run 的终态占比就落
        # COMPLETED，会把还没派发到的 40 URL 永久截断。
        if seed_count > 0 and total < seed_count:
            if batch.started_at:
                elapsed = (datetime.now(UTC) - batch.started_at).total_seconds()
                if elapsed > self.INCOMPLETE_DISPATCH_TIMEOUT_SECONDS:
                    if await self._cas_terminate(batch, BatchStatus.FAILED.value):
                        logger.warning(
                            f"batch seed 未派完超时 FAILED: batch_id={batch.public_id} "
                            f"total={total} seed={seed_count} elapsed={elapsed:.0f}s"
                        )
                    return
            # 未超时：让 batch_dispatcher/redispatch 继续追派剩余 URL，本轮不动。
            logger.debug(f"batch seed 未派完，等待派发: batch_id={batch.public_id} total={total} seed={seed_count}")
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
            logger.info(
                f"batch 状态推导: batch_id={batch.public_id} status={new_status} "
                f"total={total} seed={seed_count} success={success} "
                f"failed={failed} cancelled={cancelled}"
            )

    @staticmethod
    async def _sync_progress(
        batch: CrawlBatch,
        stat: dict[str, int],
        project_public_id: str,
        seed_count: int,
    ) -> None:
        """R2 seam-5: 把 run 状态聚合覆写到批次进度 hash。

        计数映射（progress 只有 4 个计数位）：
        - completed_urls = SUCCESS run 数
        - failed_urls    = FAILED/TIMEOUT/REJECTED + CANCELLED run 数
          （CANCELLED 归入 failed 侧，保证 total = completed + failed + pending
          恒等式对消费者成立）
        - pending_urls   = 分母 - completed - failed（含仍在跑的 run 和
          尚未派发出去的 seed）
        - total_urls     = max(seed 数, 已建 run 数)——正常时两者相等；
          重复派发（at-least-once 副作用）时取 run 数避免 pending 变负。
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
