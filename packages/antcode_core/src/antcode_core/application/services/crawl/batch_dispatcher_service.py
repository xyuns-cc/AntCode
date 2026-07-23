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

from loguru import logger
from tortoise import Tortoise

from antcode_core.domain.models import CrawlBatch, Project, TaskRun
from antcode_core.domain.models.enums import (
    BatchStatus,
    DispatchStatus,
    ProjectType,
    TaskStatus,
)
from antcode_core.domain.models.project import ProjectRule

DEFAULT_CRAWL_TASK_TIMEOUT_SECONDS = 3600


class CrawlBatchDispatcherService:
    """响应批次生命周期事件，把 seed URLs 派发到 worker。"""

    async def handle_batch_event(self, event: str, batch_id: str) -> None:
        """事件入口。event ∈ {batch_started, batch_paused, batch_resumed, batch_cancelled}。

        P1-14 (审查报告): 原实现 ``try/except`` 全吞后 return None，
        ``scheduler_event_loop`` 就 ACK 掉消息，事件永久丢失但 DB 已改。
        改为异常直接向上抛：由 ``scheduler_event_loop`` 的 XPENDING +
        deliver_count 死信机制处理重试（默认 5 次后进死信）。
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
        # NOTE: 不再 try/except 吞异常。让 scheduler_event_loop 感知失败以
        # 触发 PEL 重投（未 ACK 消息 XPENDING 累计 deliver_count 超阈值后
        # 才 ACK 进死信），保证事件"至少一次"而非"至多一次"。
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
            f"batch 派发完成: batch_id={batch_id} dispatched={dispatched} failed={failed} total={len(pending_urls)}"
        )
        # 任一 seed 既未直派成功、也未获得 durable redispatch intent 时，
        # batch_started 都不能 ACK。成功 seed 已有 TaskRun，重投时会被
        # _already_dispatched_urls 跳过；失败 seed 会在下一轮单独重试。
        if failed > 0:
            raise RuntimeError(
                f"batch 派发存在未持久化失败: batch_id={batch_id} failed={failed} "
                f"dispatched={dispatched} "
                f"total={len(pending_urls)} —— 事件将保留 PEL 由 reclaim 重投"
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

        P1-round6 5.2: 原实现只取一次快照,`_on_batch_started` 和
        `_on_batch_cancelled` 事件并发时,快照后新建的 run 会被漏掉继续执行。
        改为 loop-until-empty: 每轮拿最新 active runs,减去已处理集合,新增
        为空才退出;上游 batch.status 已 CANCELLED, 后续 `_on_batch_started`
        会被 L76 拦住不再产新 run, 循环必然收敛。
        """
        now = datetime.now(UTC)
        seen: set[str] = set()
        cancelled = 0
        control_sent = 0
        failed_run_ids: list[str] = []
        max_rounds = 5  # 硬止损: 上游收敛后 1-2 轮足够, 5 轮避免病态 loop
        for round_idx in range(max_rounds):
            active_run_ids = await self._active_run_ids_for_batch(batch_id)
            new_run_ids = [rid for rid in active_run_ids if rid not in seen]
            if not new_run_ids:
                if round_idx == 0:
                    logger.info(f"batch 无活跃 run,取消 no-op: batch_id={batch_id}")
                break
            for run_id in new_run_ids:
                ok, sent = await self._cancel_active_run(run_id, batch_id, now)
                seen.add(run_id)
                if ok:
                    cancelled += 1
                    control_sent += int(sent)
                else:
                    failed_run_ids.append(run_id)
        if seen:
            logger.info(
                f"batch 取消: batch_id={batch_id} cancelled_runs={cancelled}/{len(seen)} control_sent={control_sent}"
            )
        if failed_run_ids:
            raise RuntimeError(f"batch 取消未完成: batch_id={batch_id} failed_run_ids={failed_run_ids}; 保留 PEL 重投")

    async def _cancel_active_run(self, run_id: str, batch_id: str, now: datetime) -> tuple[bool, bool]:
        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )
        from antcode_core.domain.models.enums import RuntimeStatus

        try:
            control_sent = await self._send_worker_cancel(run_id, reason=f"batch_cancelled:{batch_id}")
        except Exception as exc:
            logger.warning(f"batch 取消发送 Worker control 失败: batch_id={batch_id} run_id={run_id} exc={exc}")
            return False, False
        if control_sent is False:
            return False, False
        updated = await execution_status_service.update_runtime_status(
            run_id=run_id,
            status=RuntimeStatus.CANCELLED,
            status_at=now,
            error_message=f"batch cancelled: {batch_id}",
        )
        return bool(updated), control_sent is True

    async def _send_worker_cancel(self, run_id: str, reason: str) -> bool | None:
        """P1-07：复用单 run 取消的 control 通道给 batch 里的每个活跃 run。

        ``None`` 表示无需/无法发送 control——尚未分配 Worker，或分配的
        Worker 记录已删除（control 永久不可达），调用方可直接置
        CANCELLED；``False`` 表示已分配但 control 暂时无法投递（如 Redis
        不可用），此时调用方不得写 CANCELLED，保留 PEL 重投。
        """
        from antcode_core.application.services.runtime.runtime_control_service import (
            write_control_event,
        )
        from antcode_core.application.services.workers.worker_service import worker_service
        from antcode_core.domain.models import TaskRun
        from antcode_core.infrastructure.redis import get_redis_client
        from antcode_core.infrastructure.redis.control_plane import (
            build_cancel_control_payload,
            control_stream,
        )

        exe = await TaskRun.filter(run_id=run_id).only("id", "run_id", "worker_id").first()
        if exe is None or not exe.worker_id:
            return None
        worker = await worker_service.get_worker_by_id(exe.worker_id)
        if worker is None:
            # C4: Worker 记录已删除 → control 永久不可达。若按 False 处理，
            # _on_batch_cancelled 会永远重投失败，幽灵活跃 run 阻塞项目
            # 删除。跳过 control，允许直接置 CANCELLED。
            logger.warning(
                f"batch 取消: 分配的 Worker 记录已不存在，跳过 control 直接取消: "
                f"run_id={run_id} worker_id={exe.worker_id}"
            )
            return None
        redis = await get_redis_client()
        if redis is None:
            return False
        payload = build_cancel_control_payload(run_id=run_id, reason=reason)
        await write_control_event(redis, control_stream(worker.public_id), payload)
        return True

    # ---------- helpers ----------

    async def _dispatch_single_url(
        self,
        batch: CrawlBatch,
        project: Project,
        rule: ProjectRule,
        url: str,
    ) -> bool:
        """把单个 URL 作为一个 rule 任务派发。TaskRun.result_data 存 batch_id 用于关联。

        P1-14 (审查报告): 派发流程做了 URL 幂等清理：
        - 保持 ``TaskRun.create → dispatch`` 的原顺序（这样 worker 早期状态
          回写有目标 TaskRun 可 update，避免竞态）。
        - 但 dispatch **失败且补派入队也失败**时，改为 **删除刚建的 TaskRun**
          （旧实现是 UPDATE status=FAILED），这样 ``_already_dispatched_urls``
          不会再把这个 URL 当"已派发"，下次 ``batch_resumed`` 能重试。
        - 外层 Exception 同理：清理孤儿 TaskRun，异常继续向上抛让 ingest
          loop 走 PEL 重投。
        """
        # 组装 rule dict（覆盖 target_url 为本次 URL）
        rule_dict = rule.to_dispatch_dict()
        rule_dict["target_url"] = url
        rule_dict.update(self._batch_rule_overrides(batch))

        run_id = self._generate_run_id(batch.public_id)
        task_run_created = False
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
            task_run_created = True

            # 派发
            from antcode_core.application.services.workers import worker_task_dispatcher

            # R1-P0-1 (审查报告): worker engine._build_task_payload 里
            # ``kwargs = params.get("kwargs", {}) if isinstance(params.get("kwargs", {}), dict) else params``
            # 当 params.kwargs 缺失时三元取空 dict 而非兜底整个 params，
            # RulePlugin.validate 就报"缺少 target_url/extraction_rules"。
            # 与 spider_dispatcher.py 的 P16 修复对齐——rule_detail 必须
            # 塞在 ``params.kwargs`` 里。crawl_batch_id 保留顶层供审计追溯。
            # batch.timeout 是单次 HTTP DOWNLOAD_TIMEOUT，不能复用成整个爬虫
            # 进程的任务超时。批次默认请求超时 30 秒，但总任务通常远超 30 秒。
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
                        attempts=0,
                        reason=err_msg,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"入补派队列失败: {exc}")
                    enqueued = False

                if not enqueued:
                    # P1-14: 补派也不可用 → 删除刚建的 TaskRun 占位，让
                    # ``_already_dispatched_urls`` 不再把这个 URL 视为已派发。
                    # 下次 batch_resumed 能重派；避免旧实现里 FAILED 占位
                    # 使 URL 永久跳过的"重放死锁"。
                    await self._delete_task_run(run_id)
                    return False
                # 已入补派队列：run 仍处于 PENDING/DISPATCHING，等 loop 重试
                return True
            return True
        except Exception as exc:
            logger.exception(f"batch URL 派发异常: batch_id={batch.public_id} url={url} err={exc}")
            # P1-14: 出异常且已建 TaskRun 时清孤儿，让 URL 可重派
            if task_run_created:
                await self._delete_task_run(run_id)
            return False

    async def _delete_task_run(self, run_id: str) -> None:
        """删除派发失败留下的 TaskRun 占位（吞掉删除异常，不影响主流程）。"""
        try:
            await TaskRun.filter(run_id=run_id).delete()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"删除失败 TaskRun 占位失败: run_id={run_id} err={exc}")

    @classmethod
    def _batch_rule_overrides(cls, batch: CrawlBatch) -> dict[str, int]:
        """显式转换批次配置；无效值必须阻止派发。"""
        mappings = (
            ("max_pages", "max_pages"),
            ("max_retries", "retry_count"),
            ("timeout", "timeout"),
            ("max_concurrency", "concurrent_requests"),
            ("max_depth", "max_depth"),
        )
        overrides = {
            target: cls._batch_int_value(batch, source)
            for source, target in mappings
            if getattr(batch, source) is not None
        }
        if batch.request_delay is not None:
            try:
                overrides["request_delay"] = int(float(batch.request_delay) * 1000)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"batch 配置无效: batch_id={batch.public_id} field=request_delay value={batch.request_delay!r}"
                ) from exc
        return overrides

    @staticmethod
    def _batch_int_value(batch: CrawlBatch, field: str) -> int:
        value = getattr(batch, field)
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"batch 配置无效: batch_id={batch.public_id} field={field} value={value!r}") from exc

    async def _already_dispatched_urls(self, batch_id: str) -> set[str]:
        """幂等派发：读取该 batch 已有 TaskRun 的 seed_url 集合。"""
        rows = await self._query_batch_runs(
            "SELECT result_data->>'seed_url' AS seed_url FROM task_executions "
            "WHERE result_data->>'crawl_batch_id' = $1 "
            "AND result_data->>'seed_url' IS NOT NULL",
            [batch_id],
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

    @staticmethod
    def _generate_run_id(batch_id: str) -> str:
        return f"batch-{batch_id}-{uuid.uuid4().hex[:16]}"


crawl_batch_dispatcher_service = CrawlBatchDispatcherService()
