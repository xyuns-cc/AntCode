"""Crawl 批次调度服务。

F: master 端接收 ``batch_started`` / ``batch_paused`` / ``batch_resumed``
/ ``batch_cancelled`` 事件，把批次里的 seed URLs 逐个派发为 rule 任务。

架构决策：每个 seed URL 作为独立 rule 任务，复用 Worker 调度链路；关联走
``TaskRun.result_data["crawl_batch_id"]``，不改 model schema。批次状态推导
（RUNNING → COMPLETED/FAILED）尚未实现，由用户手动取消或关闭。
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from loguru import logger
from tortoise import Tortoise

from antcode_core.application.services.crawl.batch_aggregate_lock import (
    crawl_batch_aggregate_lock,
)
from antcode_core.application.services.crawl.batch_cancel_control import send_batch_run_cancel
from antcode_core.application.services.crawl.batch_dispatch_state import (
    SeedDispatchOutcome,
    crawl_batch_run_id,
    is_recoverable_dispatch,
    mark_dispatch_succeeded,
    mark_redispatch_enqueued,
)
from antcode_core.application.services.crawl.batch_rule_options import batch_rule_overrides, batch_seed_slots
from antcode_core.application.services.scheduler.rule_dispatch_constraints import (
    resolve_rule_dispatch_constraints,
)
from antcode_core.application.services.scheduler_authority_epoch import (
    active_scheduler_dispatch_epoch,
)
from antcode_core.domain.models import CrawlBatch, Project, TaskRun
from antcode_core.domain.models.enums import (
    BatchStatus,
    DispatchStatus,
    ProjectType,
    TaskStatus,
)
from antcode_core.domain.models.project import ProjectRule
from antcode_core.domain.models.task_run import TASK_ID_ABSENT

DEFAULT_CRAWL_TASK_TIMEOUT_SECONDS = 3600


class CrawlBatchDispatcherService:
    """响应批次生命周期事件，把 seed URLs 派发到 worker。"""

    async def handle_batch_event(self, event: str, batch_id: str) -> None:
        """事件入口。event ∈ {batch_started, batch_paused, batch_resumed, batch_cancelled}。

        异常一律向上抛：吞掉就等于 ``scheduler_event_loop`` ACK 掉消息，事件
        永久丢失而 DB 已改。抛出后由它的 XPENDING + deliver_count 死信机制重投，
        保证"至少一次"而非"至多一次"。
        """
        if not batch_id:
            logger.warning(f"crawl batch 事件缺 batch_id: {event}")
            return
        handler = {
            "batch_started": self._on_batch_started,
            "batch_resumed": self._on_batch_resumed,
            "batch_paused": self._on_batch_paused,
            "batch_cancelled": self._on_batch_cancelled,
        }.get(event)
        if handler is None:
            logger.debug(f"crawl batch 事件未处理: {event}")
            return
        async with crawl_batch_aggregate_lock(batch_id):
            await handler(batch_id)

    # ---------- start / resume ----------

    async def _on_batch_started(self, batch_id: str) -> None:
        """派发 seed URLs 到 worker。幂等：已派发过的 URL 跳过。"""
        batch = await CrawlBatch.get_or_none(public_id=batch_id)
        if batch is None:
            logger.warning(f"crawl batch 不存在: {batch_id}")
            return
        if batch.status not in (BatchStatus.RUNNING.value, BatchStatus.RUNNING):
            logger.info(f"batch 非 RUNNING 状态，跳过派发: batch_id={batch_id} status={batch.status}")
            return

        project = await Project.get_or_none(id=batch.project_id)
        if project is None or project.type != ProjectType.RULE:
            logger.warning(
                f"batch 关联项目非规则项目，跳过: batch_id={batch_id} project_type={getattr(project, 'type', None)}"
            )
            return

        rule = await ProjectRule.get_or_none(project_id=batch.project_id)
        if rule is None:
            logger.error(f"规则项目缺 ProjectRule 详情: project_id={batch.project_id}")
            return

        # 只派发尚未派发的 URL
        already_dispatched = await self._already_dispatched_urls(batch_id)
        seeds = [u for u in (batch.seed_urls or []) if u]
        pending_urls = [u for u in seeds if u not in already_dispatched]

        if not pending_urls:
            logger.info(f"batch 无待派发 URL: batch_id={batch_id}")
            return

        # B12: 批次派发不持有 Leader 身份，代际回读权威表并整批共用一个；
        # 读不到活跃 Master 权威时整个事件失败，保留 PEL 由 reclaim 重投。
        async with active_scheduler_dispatch_epoch():
            tally = await self._dispatch_pending_urls(batch, project, rule, urls=pending_urls, seed_count=len(seeds))
        self._log_dispatch_tally(batch_id, tally, total=len(pending_urls))
        # 任一 seed 既未直派成功、也未获得 durable redispatch intent 时，
        # batch_started 都不能 ACK。成功 seed 已有 TaskRun，重投时会被
        # _already_dispatched_urls 跳过；失败 seed 会在下一轮单独重试。
        failed = tally[SeedDispatchOutcome.FAILED]
        if failed > 0:
            raise RuntimeError(
                f"batch 派发存在未持久化失败: batch_id={batch_id} failed={failed} "
                f"dispatched={tally[SeedDispatchOutcome.DISPATCHED]} "
                f"total={len(pending_urls)} —— 事件将保留 PEL 由 reclaim 重投"
            )

    @staticmethod
    def _log_dispatch_tally(batch_id: str, tally: Counter[SeedDispatchOutcome], *, total: int) -> None:
        """日志口径必须与真实结果一致：入补派队列不能算成 dispatched。

        只要有 seed 没能直派成功（无论是进了补派队列还是彻底失败），这行就
        必须是 WARNING —— 运维扫日志时"这批派发正常"与否不该靠自己算差值。
        """
        counts = " ".join(f"{outcome.value}={tally[outcome]}" for outcome in SeedDispatchOutcome)
        summary = f"batch 派发结束: batch_id={batch_id} total={total} {counts}"
        degraded = tally[SeedDispatchOutcome.REDISPATCH_ENQUEUED] + tally[SeedDispatchOutcome.FAILED]
        if degraded:
            logger.warning(summary)
            return
        logger.info(summary)

    async def _dispatch_pending_urls(
        self,
        batch: CrawlBatch,
        project: Project,
        rule: ProjectRule,
        *,
        urls: list[str],
        seed_count: int,
    ) -> Counter[SeedDispatchOutcome]:
        """在调用方已打开的 Master 代际下派发 URL，按结果分类计数。

        超额度的 seed 记 DEFERRED 不派发，槽位空出来后由 crawl_batch_status_loop
        追派：seed 数多于额度时每 seed 并发已被地板顶到 1，只剩压住并行 seed 数
        这一个手段能让总并发不超额度。
        """
        tally: Counter[SeedDispatchOutcome] = Counter()
        slots = batch_seed_slots(batch, seed_count=seed_count)
        budget = max(slots - len(await self._active_run_ids_for_batch(batch.public_id)), 0)
        tally[SeedDispatchOutcome.DEFERRED] = max(len(urls) - budget, 0)
        for url in urls[:budget]:
            tally[await self._dispatch_single_url(batch, project, rule, url, seed_count=seed_count)] += 1
        return tally

    async def _on_batch_resumed(self, batch_id: str) -> None:
        # 恢复 = 派发剩余未完成/未派发的 URL；用同一逻辑
        await self._on_batch_started(batch_id)

    async def _on_batch_paused(self, batch_id: str) -> None:
        """暂停：停止派发新 URL。已派发的 task 继续跑（不做强制中断，保护已完成的抓取）。"""
        logger.info(f"batch 已暂停，不再派发新 URL: batch_id={batch_id}")

    # ---------- cancel ----------

    async def _on_batch_cancelled(self, batch_id: str) -> None:
        """取消：把该 batch 已派发但未终态的 TaskRun 置 CANCELLED。

        走 ``execution_status_service.update_runtime_status`` 而不是裸
        ``filter().update()``：后者无状态 CAS 且只写 status，而终态吸收保护全靠
        runtime_status，迟到的 SUCCESS 会穿过闸门把状态翻回。调用方已持有 batch
        aggregate 的 advisory lock，锁内读一次 active snapshot 即可。
        """
        now = datetime.now(UTC)
        cancelled = 0
        control_sent = 0
        failed_run_ids: list[str] = []
        active_run_ids = await self._active_run_ids_for_batch(batch_id)
        for run_id in active_run_ids:
            ok, sent = await self._cancel_active_run(run_id, batch_id, now)
            if ok:
                cancelled += 1
                control_sent += int(sent)
            else:
                failed_run_ids.append(run_id)
        if active_run_ids:
            logger.info(
                f"batch 取消: batch_id={batch_id} cancelled_runs={cancelled}/{len(active_run_ids)} "
                f"control_sent={control_sent}"
            )
        else:
            logger.info(f"batch 无活跃 run,取消 no-op: batch_id={batch_id}")
        if failed_run_ids:
            raise RuntimeError(f"batch 取消未完成: batch_id={batch_id} failed_run_ids={failed_run_ids}; 保留 PEL 重投")

    async def _cancel_active_run(self, run_id: str, batch_id: str, now: datetime) -> tuple[bool, bool]:
        from antcode_core.application.services.scheduler.cancel_request_service import record_cancel_request
        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )
        from antcode_core.domain.models.enums import RuntimeStatus

        if not await record_cancel_request(run_id, requested_by=None, requested_at=now):
            return True, False
        try:
            control_sent = await send_batch_run_cancel(run_id, reason=f"batch_cancelled:{batch_id}")
        except Exception as exc:
            logger.warning(f"batch 取消发送 Worker control 失败: batch_id={batch_id} run_id={run_id} exc={exc}")
            return False, False
        if control_sent is False:
            return False, False
        if control_sent is True:
            return True, True
        updated = await execution_status_service.update_runtime_status(
            run_id=run_id,
            status=RuntimeStatus.CANCELLED,
            status_at=now,
            error_message=f"batch cancelled: {batch_id}",
        )
        return bool(updated), False

    # ---------- helpers ----------

    async def _dispatch_single_url(
        self,
        batch: CrawlBatch,
        project: Project,
        rule: ProjectRule,
        url: str,
        *,
        seed_count: int,
    ) -> SeedDispatchOutcome:
        """把单个 URL 作为一个 rule 任务派发。TaskRun.result_data 存 batch_id 用于关联。

        run ID 由 batch + seed URL 确定，崩溃重放复用原 TaskRun；``create →
        dispatch`` 的顺序保证 Worker 早期回写有目标。派发与补派双双失败时
        **删掉**刚建的 TaskRun 而不是置 FAILED，否则 ``_already_dispatched_urls``
        会把这个 URL 当已派发，下次 ``batch_resumed`` 再也重试不到它。
        """
        # 组装 rule dict（覆盖 target_url 为本次 URL）
        rule_dict = rule.to_dispatch_dict()
        rule_dict["target_url"] = url
        rule_dict.update(batch_rule_overrides(batch, seed_count=seed_count))
        constraints = resolve_rule_dispatch_constraints(rule, rule_dict)

        run_id = crawl_batch_run_id(batch.public_id, url)
        task_run_prepared = False
        dispatch_durable = False
        try:
            existing = await TaskRun.get_or_none(run_id=run_id)
            if existing is not None and not is_recoverable_dispatch(existing):
                return SeedDispatchOutcome.ALREADY_DISPATCHED
            if existing is None:
                # 所有者由 result_data.crawl_batch_id 反查 CrawlBatch.user_id 得到
                # （dispatch_authorization / spider_run_access 同一契约）。批次 run
                # 没有 Task 行，TASK_ID_ABSENT 是这个契约的显式标记。
                await TaskRun.create(
                    run_id=run_id,
                    task_id=TASK_ID_ABSENT,
                    status=TaskStatus.PENDING,
                    dispatch_status=DispatchStatus.PENDING,
                    start_time=None,
                    result_data={
                        "crawl_batch_id": batch.public_id,
                        "seed_url": url,
                    },
                )
            task_run_prepared = True

            # 派发
            from antcode_core.application.services.workers import worker_task_dispatcher

            # rule_detail 必须塞进 ``params.kwargs``，否则 worker engine._build_payload
            # 取到空 dict 报"缺少 target_url"。batch.timeout 是单次 HTTP 超时，不是任务超时。
            task_timeout = DEFAULT_CRAWL_TASK_TIMEOUT_SECONDS
            result = await worker_task_dispatcher.dispatch_task(
                project_id=project.public_id,
                run_id=run_id,
                params={
                    "kwargs": {"rule_detail": rule_dict},
                    "crawl_batch_id": batch.public_id,
                },
                environment_vars=None,
                timeout=task_timeout,
                project_type="rule",
                region=constraints.region,
                require_render=constraints.require_render,
            )

            if not getattr(result, "success", False):
                err_msg = getattr(result, "error", "派发失败")
                logger.warning(f"batch URL 派发失败: batch_id={batch.public_id} url={url} err={err_msg}")
                # T7-B3a (P1-1): 派发失败入补派队列，退避后由 RedispatchLoop
                # 重试；不再直接置 FAILED（超阈值时 loop 会自己落 FAILED）
                try:
                    from antcode_core.application.services.scheduler.redispatch_service import (
                        redispatch_service,
                    )

                    enqueued = await redispatch_service.enqueue(
                        run_id=run_id,
                        project_id=project.public_id,
                        params={
                            "kwargs": {"rule_detail": rule_dict},
                            "crawl_batch_id": batch.public_id,
                        },
                        environment_vars=None,
                        timeout=task_timeout,
                        project_type="rule",
                        region=constraints.region,
                        require_render=constraints.require_render,
                        attempts=0,
                        reason=err_msg,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"入补派队列失败: {exc}")
                    enqueued = False

                if not enqueued:
                    # P1-14: 补派也不可用 → 删掉刚建的 TaskRun 占位，让 URL 不再被
                    # ``_already_dispatched_urls`` 视为已派发，下次 resume 还能重派。
                    await self._delete_task_run(run_id)
                    return SeedDispatchOutcome.FAILED
                # 已入补派队列：run 仍处于 PENDING/DISPATCHING，等 loop 重试
                dispatch_durable = True
                await mark_redispatch_enqueued(run_id)
                return SeedDispatchOutcome.REDISPATCH_ENQUEUED
            dispatch_durable = True
            await mark_dispatch_succeeded(run_id)
            return SeedDispatchOutcome.DISPATCHED
        except Exception as exc:
            logger.exception(f"batch URL 派发异常: batch_id={batch.public_id} url={url} err={exc}")
            # P1-14: 出异常且已建 TaskRun 时清孤儿，让 URL 可重派
            if task_run_prepared and not dispatch_durable:
                await self._delete_task_run(run_id)
            return SeedDispatchOutcome.FAILED

    async def _delete_task_run(self, run_id: str) -> None:
        """删除派发失败留下的 TaskRun 占位；存储错误必须上抛重投。"""
        await TaskRun.filter(run_id=run_id).delete()

    async def _already_dispatched_urls(self, batch_id: str) -> set[str]:
        """幂等派发：读取该 batch 已有 TaskRun 的 seed_url 集合。"""
        rows = await self._query_batch_runs(
            "SELECT result_data->>'seed_url' AS seed_url FROM task_executions "
            "WHERE result_data->>'crawl_batch_id' = $1 "
            "AND result_data->>'seed_url' IS NOT NULL "
            "AND NOT (status = $2 AND dispatch_status IN ($3, $4) AND runtime_status IS NULL)",
            [
                batch_id,
                TaskStatus.PENDING.value,
                DispatchStatus.PENDING.value,
                DispatchStatus.FAILED.value,
            ],
        )
        return {str(row["seed_url"]) for row in rows}

    async def _active_run_ids_for_batch(self, batch_id: str) -> list[str]:
        """在数据库中筛选该批次的非终态 run。"""
        statuses = [
            TaskStatus.PENDING.value,
            TaskStatus.DISPATCHING.value,
            TaskStatus.QUEUED.value,
            TaskStatus.RUNNING.value,
        ]
        rows = await self._query_batch_runs(
            "SELECT run_id FROM task_executions "
            "WHERE result_data->>'crawl_batch_id' = $1 "
            "AND status IN ($2, $3, $4, $5) ORDER BY id ASC",
            [batch_id, *statuses],
        )
        return [str(row["run_id"]) for row in rows]

    @staticmethod
    async def _query_batch_runs(query: str, values: list[str]) -> list[dict]:
        connection = Tortoise.get_connection("default")
        return await connection.execute_query_dict(query, values)


crawl_batch_dispatcher_service = CrawlBatchDispatcherService()
