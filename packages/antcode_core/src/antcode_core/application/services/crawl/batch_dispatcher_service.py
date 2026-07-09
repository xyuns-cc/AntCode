"""Crawl 批次调度服务。

F: master 端接收 ``batch_started`` / ``batch_paused`` / ``batch_resumed``
/ ``batch_cancelled`` 事件，把批次里的 seed URLs 逐个派发为 rule 任务。

架构决策（F 一期）：
- **每个 seed URL 派发为一个独立 rule 任务**（走 worker 的 RulePlugin），
  这样复用已有的 worker 调度 / 心跳 / lease / reclaim 链路，不再引入
  CrawlTask 的重量级 Redis 队列。
- **关联通过 TaskRun.result_data["crawl_batch_id"]**：查批次任务时按此字段
  过滤（结合 Task 的 name/tags 也够用）。不改 model schema。
- 状态推导（RUNNING → COMPLETED/FAILED）由独立 loop 定期扫，一期先不实现，
  batch 保持 RUNNING 状态，由用户手动取消或所有 seed 完成后手动关闭。

不做的：
- CrawlTask 的 Redis 优先级队列（保留代码但不激活）；未来做深度爬取时用。
- 深度爬取（RuleSpider 只做一层）；一期只跑 seed URLs。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.domain.models import CrawlBatch, Project, TaskRun
from antcode_core.domain.models.enums import (
    BatchStatus,
    DispatchStatus,
    ProjectType,
    TaskStatus,
)
from antcode_core.domain.models.project import ProjectRule


class CrawlBatchDispatcherService:
    """响应批次生命周期事件，把 seed URLs 派发到 worker。"""

    async def handle_batch_event(self, event: str, batch_id: str) -> None:
        """事件入口。event ∈ {batch_started, batch_paused, batch_resumed, batch_cancelled}。"""
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
        try:
            await handler(batch_id)
        except Exception as exc:
            logger.exception(
                f"crawl batch 事件处理失败: event={event} batch_id={batch_id} err={exc}"
            )

    # ---------- start / resume ----------

    async def _on_batch_started(self, batch_id: str) -> None:
        """派发 seed URLs 到 worker。幂等：已派发过的 URL 跳过。"""
        batch = await CrawlBatch.get_or_none(public_id=batch_id)
        if batch is None:
            logger.warning(f"crawl batch 不存在: {batch_id}")
            return
        if batch.status not in (BatchStatus.RUNNING.value, BatchStatus.RUNNING):
            logger.info(
                f"batch 非 RUNNING 状态，跳过派发: batch_id={batch_id} status={batch.status}"
            )
            return

        project = await Project.get_or_none(id=batch.project_id)
        if project is None or project.type != ProjectType.RULE:
            logger.warning(
                f"batch 关联项目非规则项目，跳过: batch_id={batch_id} "
                f"project_type={getattr(project, 'type', None)}"
            )
            return

        rule = await ProjectRule.get_or_none(project_id=batch.project_id)
        if rule is None:
            logger.error(f"规则项目缺 ProjectRule 详情: project_id={batch.project_id}")
            return

        # 只派发尚未派发的 URL
        already_dispatched = await self._already_dispatched_urls(batch_id)
        seed_urls = list(batch.seed_urls or [])
        pending_urls = [u for u in seed_urls if u and u not in already_dispatched]

        if not pending_urls:
            logger.info(f"batch 无待派发 URL: batch_id={batch_id}")
            return

        dispatched = 0
        failed = 0
        for url in pending_urls:
            ok = await self._dispatch_single_url(batch, project, rule, url)
            if ok:
                dispatched += 1
            else:
                failed += 1
        logger.info(
            f"batch 派发完成: batch_id={batch_id} dispatched={dispatched} "
            f"failed={failed} total={len(pending_urls)}"
        )

    async def _on_batch_resumed(self, batch_id: str) -> None:
        # 恢复 = 派发剩余未完成/未派发的 URL；用同一逻辑
        await self._on_batch_started(batch_id)

    async def _on_batch_paused(self, batch_id: str) -> None:
        """暂停：停止派发新 URL。已派发的 task 继续跑（不做强制中断，保护已完成的抓取）。"""
        logger.info(f"batch 已暂停，不再派发新 URL: batch_id={batch_id}")

    # ---------- cancel ----------

    async def _on_batch_cancelled(self, batch_id: str) -> None:
        """取消：把该 batch 已派发但未终态的 TaskRun 置 CANCELLED。

        R1-P1-1 (审查报告): 原实现直接 ``filter().update(status=CANCELLED)``——
        (a) 无状态条件 CAS，已终态的记录也会被覆盖；
        (b) 只写 status 不写 runtime_status，而
            ``execution_status_service._should_update`` 的终态吸收保护完全基于
            runtime_status；迟到的 SUCCESS 报告会**穿过闸门**把状态翻回。
        改走 ``execution_status_service.update_runtime_status``：它带条件 CAS +
        同步写 status/runtime_status/timestamps，能守住终态。
        """
        active_runs = await self._active_runs_for_batch(batch_id)
        if not active_runs:
            logger.info(f"batch 无活跃 run，取消 no-op: batch_id={batch_id}")
            return

        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )
        from antcode_core.domain.models.enums import RuntimeStatus

        now = datetime.now(UTC)
        cancelled = 0
        for run in active_runs:
            ok = await execution_status_service.update_runtime_status(
                run_id=run.run_id,
                status=RuntimeStatus.CANCELLED,
                status_at=now,
                error_message=f"batch cancelled: {batch_id}",
            )
            if ok:
                cancelled += 1
        logger.info(
            f"batch 取消: batch_id={batch_id} cancelled_runs={cancelled}/{len(active_runs)}"
        )
        # 已实际派发到 worker 的运行中任务，还需要发 control 取消给 worker。
        # 一期先只标 DB，让 worker 侧的 heartbeat + reclaim 兜底；等 F1-B 再补 control。

    # ---------- helpers ----------

    async def _dispatch_single_url(
        self,
        batch: "CrawlBatch",
        project: "Project",
        rule: "ProjectRule",
        url: str,
    ) -> bool:
        """把单个 URL 作为一个 rule 任务派发。TaskRun.result_data 存 batch_id 用于关联。"""
        # 组装 rule dict（覆盖 target_url 为本次 URL）
        rule_dict = rule.to_dispatch_dict()
        rule_dict["target_url"] = url

        run_id = self._generate_run_id(batch.public_id)
        try:
            # 先建 TaskRun 占位（关联批次；task_id 留 0 表示 batch-issued）
            await TaskRun.create(
                run_id=run_id,
                task_id=0,  # F 一期：批次任务不挂 Task 表
                status=TaskStatus.PENDING,
                dispatch_status=DispatchStatus.PENDING,
                start_time=None,
                result_data={
                    "crawl_batch_id": batch.public_id,
                    "seed_url": url,
                },
                created_by=batch.user_id,
            )

            # 派发
            from antcode_core.application.services.workers import worker_task_dispatcher

            # R1-P0-1 (审查报告): worker engine._build_task_payload 里
            # ``kwargs = params.get("kwargs", {}) if isinstance(params.get("kwargs", {}), dict) else params``
            # 当 params.kwargs 缺失时三元取空 dict 而非兜底整个 params，
            # RulePlugin.validate 就报"缺少 target_url/extraction_rules"。
            # 与 spider_dispatcher.py 的 P16 修复对齐——rule_detail 必须
            # 塞在 ``params.kwargs`` 里。crawl_batch_id 保留顶层供审计追溯。
            # R1-P1-3: timeout 用独立任务级字段（batch.task_timeout），不再
            # 复用 ``batch.timeout``（那是 HTTP 请求超时，默认 30s，容易杀
            # 掉慢站点抓取）。
            task_timeout = int(
                getattr(batch, "task_timeout", None)
                or 3600
            )
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
            )

            if not getattr(result, "success", False):
                err_msg = getattr(result, "error", "派发失败")
                logger.warning(
                    f"batch URL 派发失败: batch_id={batch.public_id} url={url} "
                    f"err={err_msg}"
                )
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
                        attempts=0,
                        reason=err_msg,
                    )
                except Exception as exc:
                    logger.warning(f"入补派队列失败，直接置 FAILED: {exc}")
                    enqueued = False

                if not enqueued:
                    # 补派服务不可用或超阈值 → 老路径兜底
                    await TaskRun.filter(run_id=run_id).update(
                        status=TaskStatus.FAILED,
                        end_time=datetime.now(UTC),
                        error_message=err_msg,
                    )
                    return False
                # 已入补派队列：run 仍处于 PENDING/DISPATCHING，等 loop 重试
                return True
            return True
        except Exception as exc:
            logger.exception(
                f"batch URL 派发异常: batch_id={batch.public_id} url={url} err={exc}"
            )
            return False

    async def _already_dispatched_urls(self, batch_id: str) -> set[str]:
        """幂等派发：读取该 batch 已有 TaskRun 的 seed_url 集合。"""
        # tortoise JSONField 查询：走原始 SQL 会更精准，一期先扫全部再过滤
        # （seed_urls 通常几百到几千，量级可控）
        runs = await TaskRun.all().values_list("result_data", flat=True)
        already: set[str] = set()
        for data in runs:
            if not isinstance(data, dict):
                continue
            if data.get("crawl_batch_id") != batch_id:
                continue
            url = data.get("seed_url")
            if isinstance(url, str):
                already.add(url)
        return already

    async def _active_runs_for_batch(self, batch_id: str) -> list[TaskRun]:
        """扫非终态且关联该 batch 的 run。终态集合参考 execution_status_service。"""
        non_terminal = [
            TaskStatus.PENDING,
            TaskStatus.DISPATCHING,
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        ]
        candidates = await TaskRun.filter(status__in=non_terminal).all()
        active: list[TaskRun] = []
        for run in candidates:
            data = run.result_data
            if isinstance(data, dict) and data.get("crawl_batch_id") == batch_id:
                active.append(run)
        return active

    @staticmethod
    def _generate_run_id(batch_id: str) -> str:
        return f"batch-{batch_id}-{uuid.uuid4().hex[:16]}"


crawl_batch_dispatcher_service = CrawlBatchDispatcherService()
