"""节点任务分发器 - 智能负载均衡与项目同步

支持可选的 TaskQueueBackend 集成，用于 Master 端任务队列管理。
当启用时，任务会先入队到 Master 的队列，然后再分发到 Worker 节点。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from antcode_core.application.services import lease_fenced_ready_publish as ready_publish
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.workers.dispatch_task_enrichment import (
    RUNTIME_ENV_KEY,
    enrich_dispatch_tasks,
)
from antcode_core.application.services.workers.worker_capability_routing import (
    capability_requirement_label,
    has_render_capability,
    require_worker_current_requirements,
    required_execution_task_types,
    resolve_selection_capabilities,
    supports_task_types,
)
from antcode_core.application.services.workers.worker_dispatch_failure import failed_batch_dispatch
from antcode_core.application.services.workers.worker_load_score import calculate_load_score
from antcode_core.application.services.workers.worker_ranking import build_worker_rankings
from antcode_core.application.services.workers.worker_registration_gate import filter_registration_ready_workers
from antcode_core.application.services.workers.worker_resource_probe import (
    WorkerMetricsUnavailableError,
    collect_dispatch_candidates,
    probe_failure_suffix,
    probe_worker_resources,
    warn_unreadable_workers,
)
from antcode_core.application.services.workers.worker_selection import select_dispatch_worker
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.domain.models import Worker, WorkerStatus
from antcode_core.infrastructure.redis import task_ready_stream
from antcode_core.observability.tracing import get_current_trace


def _group_run_ids_by_project(tasks: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for task in tasks:
        if task.get("project_type") == "rule":
            continue
        project_id = task.get("project_id")
        run_id = task.get("run_id") or task.get("task_id")
        if not project_id or not run_id:
            continue
        project_run_ids = grouped.setdefault(project_id, [])
        if run_id not in project_run_ids:
            project_run_ids.append(run_id)
    return grouped


@dataclass
class DispatchResult:
    """单任务分发结果"""

    success: bool
    worker_id: str | None = None
    worker_name: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    message: str = ""
    error: str | None = None
    transfer_skipped: bool = False
    accepted_count: int = 0


@dataclass
class BatchDispatchResult:
    """批量任务分发结果"""

    success: bool
    worker_id: str | None = None
    worker_name: str | None = None
    batch_id: str | None = None
    accepted_count: int = 0
    rejected_count: int = 0
    accepted_tasks: list[dict] = field(default_factory=list)
    rejected_tasks: list[dict] = field(default_factory=list)
    message: str = ""
    error: str | None = None
    sync_results: dict | None = None


class WorkerLoadBalancer:
    """负载均衡器"""

    # 硬门禁：过了这条线就不再是"排在后面"，而是完全不派活。
    #
    # 这两个 90 与下面的 0.8 一样出自 70a26e9 的裸字面量，**没有任何依据留下**——同批
    # 引入的 WEIGHT_* 权重常量甚至从未被引用过。它们是按宿主口径的年代定的，而 CPU 与
    # 内存现已换成容器配额口径（f23f192 / 8074a8f），阈值的含义随之从"整机忙闲"变成
    # "本容器吃掉了自己配额的几成"。真机实测当前不误伤（三台各跑到自己配额约 50% 时全部
    # available、76.6% 仍不踢、只有真打满的那台被拦），但"实测没出事"不等于"90 是对的
    # 数"。上线首日应观察这条线附近的判定，有数据再定，不要在这里补一个编出来的理由。
    MAX_CPU_THRESHOLD = 90
    MAX_MEMORY_THRESHOLD = 90
    MAX_TASKS_RATIO = 0.8

    def __init__(self):
        self._resource_cache = {}
        self._resource_cache_time = {}
        self._resource_cache_ttl = 2.0
        self._resource_lock = asyncio.Lock()
        self._resource_inflight = {}

    def _get_cached_resources(self, worker):
        cached = self._resource_cache.get(worker.id)
        if not cached:
            return None
        cached_at = self._resource_cache_time.get(worker.id)
        if cached_at is None:
            return None
        if (asyncio.get_event_loop().time() - cached_at) > self._resource_cache_ttl:
            return None
        return cached

    async def _fetch_resources(self, worker):
        try:
            normalized = await probe_worker_resources(worker)
        # "读不到指标"必须原样冒出去。被下面这个 except 兜成 None，就又回到了
        # "Redis 抖一下 = 所有 Worker 一起消失，且没人知道为什么"。
        except WorkerMetricsUnavailableError:
            raise
        except Exception as e:
            logger.warning(f"资源指标无效: worker={worker.name}, error={e}")
            return None

        self._resource_cache[worker.id] = normalized
        self._resource_cache_time[worker.id] = asyncio.get_event_loop().time()
        return normalized

    async def _refresh_resources(self, worker):
        cached = self._get_cached_resources(worker)
        if cached is not None:
            return cached

        async with self._resource_lock:
            cached = self._get_cached_resources(worker)
            if cached is not None:
                return cached
            inflight = self._resource_inflight.get(worker.id)
            if not inflight:
                inflight = asyncio.create_task(self._fetch_resources(worker))
                self._resource_inflight[worker.id] = inflight

        try:
            return await inflight
        finally:
            async with self._resource_lock:
                if self._resource_inflight.get(worker.id) is inflight:
                    self._resource_inflight.pop(worker.id, None)

    async def score_worker(self, worker):
        """单台节点的负载评分，口径与 select_best_worker 排序时用的完全一致。"""
        return calculate_load_score(await self._refresh_resources(worker))

    def is_worker_available(self, worker, metrics=None):
        """检查节点可用性"""
        if worker.status != WorkerStatus.ONLINE:
            return False

        if metrics is None:
            metrics = self._get_cached_resources(worker)

        if not metrics:
            return False

        if metrics.get("cpu", 100) >= self.MAX_CPU_THRESHOLD:
            return False

        if metrics.get("memory", 100) >= self.MAX_MEMORY_THRESHOLD:
            return False

        running_tasks = metrics.get("runningTasks", 0)
        max_tasks = metrics.get("maxConcurrentTasks", 1)
        if max_tasks <= 0:
            return False
        return not running_tasks >= max_tasks * self.MAX_TASKS_RATIO

    async def select_best_worker(
        self,
        workers=None,
        exclude_workers=None,
        region=None,
        tags=None,
        require_render=False,
        require_task_type: str | frozenset[str] | None = None,
    ):
        """
        选择最佳节点

        参数:
        - workers: 候选节点列表（可选）
        - exclude_workers: 排除的 Worker ID 列表
        - region: 区域过滤
        - tags: 标签过滤
        - require_render: 是否需要渲染能力（DrissionPage）
        - require_task_type: T6-T4b: 要求 worker.capabilities.task_types
          包含此值（"rule" / "code" / "spider" / "render" / "file"）。
          worker 未上报或格式错误时 fail-closed，避免派给不支持的节点。
        """
        if workers is None:
            query = Worker.filter(status=WorkerStatus.ONLINE.value)
            if region:
                query = query.filter(region=region)
            workers = await query.all()

        workers = await filter_registration_ready_workers(workers)

        if not workers:
            logger.warning("无可用节点")
            return None

        capabilities = await resolve_selection_capabilities(workers, require_render, require_task_type)

        filtered_workers = []
        for worker in workers:
            if (exclude_workers and worker.id in exclude_workers) or (region and worker.region != region):
                continue

            if tags:
                worker_tags = worker.tags or []
                if not any(tag in worker_tags for tag in tags):
                    continue

            # 检查渲染能力要求
            if require_render and not has_render_capability(capabilities[worker.id]):
                logger.debug(f"节点 [{worker.name}] 无渲染能力，跳过")
                continue

            # T6-T4b: task_type capability 过滤
            if require_task_type and not supports_task_types(capabilities[worker.id], require_task_type):
                continue

            filtered_workers.append(worker)

        if not filtered_workers:
            if require_task_type:
                logger.warning(
                    f"无支持 task_type={capability_requirement_label(require_task_type)} 的可用节点 "
                    "(检查 worker 侧 WORKER_ENABLE_RULE_PLUGIN 等 env)"
                )
            elif require_render:
                logger.warning("无符合条件的渲染节点")
            else:
                logger.warning("无符合条件节点")
            return None

        resource_results = await asyncio.gather(
            *[self._refresh_resources(worker) for worker in filtered_workers],
            return_exceptions=True,
        )

        candidates = collect_dispatch_candidates(self, filtered_workers, resource_results)
        warn_unreadable_workers(candidates)

        if not candidates.scored:
            shortage = "无符合条件的渲染节点" if require_render else "无符合条件节点"
            logger.warning("{}{}", shortage, probe_failure_suffix(candidates))
            return None

        scored_workers = []
        for worker, metrics in candidates.scored:
            score = calculate_load_score(metrics)
            scored_workers.append((worker, score))
            logger.debug(f"负载评分 [{worker.name}] {score}")

        scored_workers.sort(key=lambda x: x[1])

        logger.info(f"选中节点 [{scored_workers[0][0].name}] 评分:{scored_workers[0][1]}")
        return scored_workers[0][0]

    def _has_render_capability(self, worker):
        """检查节点是否有渲染能力"""
        return has_render_capability(worker.capabilities)

    def _supports_task_type(self, worker, task_type: str | frozenset[str]) -> bool:
        """T6-T4b: worker.capabilities.task_types 里是否声明支持某 task_type。

        Worker 必须显式声明能力，缺失或格式错误时 fail-closed。
        """
        return supports_task_types(worker.capabilities, task_type)

    async def get_workers_ranking(self, region=None, top_n=10):
        """获取节点排名（只读展示，行的装配见 worker_ranking）。"""
        query = Worker.filter(status=WorkerStatus.ONLINE.value)
        if region:
            query = query.filter(region=region)
        workers = await query.all()

        resource_results = await asyncio.gather(
            *[self._refresh_resources(worker) for worker in workers],
            return_exceptions=True,
        )
        return build_worker_rankings(self, workers, resource_results)[:top_n]


class WorkerTaskDispatcher:
    """任务分发器 - 支持批量任务和优先级调度。"""

    def __init__(self):
        self.load_balancer = WorkerLoadBalancer()

    async def dispatch_task(
        self,
        project_id,
        run_id,
        params=None,
        environment_vars=None,
        timeout=3600,
        worker_id=None,
        region=None,
        tags=None,
        priority=None,
        project_type="code",
        require_render=False,
        runtime_env_name=None,
    ):
        """
        分发单个任务到节点（使用批量接口）

        参数:
        - require_render: 是否需要渲染能力（用于需要浏览器渲染的爬虫任务）
        """
        # 保留键只能通过可信顶层字段派发，不能由普通子进程环境决定 runtime。
        environment = dict(environment_vars or {})
        environment.pop(RUNTIME_ENV_KEY, None)
        task_item = {
            "task_id": run_id,
            "run_id": run_id,
            "project_id": project_id,
            "project_type": project_type,
            "priority": priority,
            "params": params or {},
            "environment": environment,
            "runtime_env_name": runtime_env_name or "",
            "timeout": timeout,
            "require_render": require_render,
        }

        result = await self.dispatch_batch(
            tasks=[task_item],
            worker_id=worker_id,
            region=region,
            tags=tags,
            require_render=require_render,
        )

        # 转换批量结果为单任务结果格式
        if result.success:
            return DispatchResult(
                success=True,
                worker_id=result.worker_id,
                worker_name=result.worker_name,
                run_id=run_id,
                task_id=run_id,
                message="任务已分发到优先级队列",
                transfer_skipped=bool(result.sync_results and result.sync_results.get("transfer_skipped")),
                accepted_count=result.accepted_count,
            )
        else:
            rejected = result.rejected_tasks
            error_msg = result.error
            if not error_msg and rejected:
                error_msg = rejected[0].get("reason", "任务被拒绝")
            if not error_msg:
                error_msg = "任务分发失败，未知原因"
            return DispatchResult(
                success=False,
                error=error_msg,
                worker_id=result.worker_id,
                worker_name=result.worker_name,
            )

    async def dispatch_batch(
        self,
        tasks,
        worker_id=None,
        region=None,
        tags=None,
        batch_id=None,
        require_render=False,
    ):
        """
        批量分发任务到节点（使用优先级队列接口）

        参数:
        - require_render: 是否需要渲染能力
        """
        import uuid

        if not tasks:
            return BatchDispatchResult(success=False, error="任务列表为空")

        # 检查任务是否需要渲染能力
        if not require_render:
            for task in tasks:
                if task.get("require_render"):
                    require_render = True
                    break

        require_task_type = required_execution_task_types(tasks)

        # 选择目标 Worker
        worker = await self._select_worker(
            worker_id,
            region,
            tags,
            require_render=require_render,
            require_task_type=require_task_type,
        )
        if not worker:
            err = "无可用 Worker"
            if require_task_type:
                label = capability_requirement_label(require_task_type)
                err = f"无支持 task_type={label} 的 Worker (检查 worker 侧插件是否装载)"
            return BatchDispatchResult(success=False, error=err)

        try:
            # 确保节点在线
            connected = await self._ensure_worker_connected(worker)
            if not connected:
                return BatchDispatchResult(success=False, error=f"Worker 未在线: {worker.name}")

            # Master 构建 source bundle；rule 定义随参数下发，不需要 bundle。
            from antcode_core.application.services.workers.source_bundle_dispatch_service import (
                source_bundle_dispatch_service,
            )

            bundle_project_ids: list[str] = []
            rule_task_ids: set[str] = set()
            for t in tasks:
                pid = t.get("project_id")
                if not pid:
                    continue
                if t.get("project_type") == "rule":
                    rule_task_ids.add(pid)
                elif pid not in bundle_project_ids:
                    bundle_project_ids.append(pid)

            run_ids_by_project = _group_run_ids_by_project(tasks)

            run_download_info: dict = {}
            sync_results: dict = {"synced": [], "skipped": [], "failed": []}
            if bundle_project_ids:
                (
                    sync_results,
                    run_download_info,
                ) = await source_bundle_dispatch_service.build_dispatch_for_worker_with_info(
                    worker,
                    bundle_project_ids,
                    run_ids_by_project=run_ids_by_project,
                )

                if sync_results.get("failed"):
                    failed_items = sync_results.get("failed", [])
                    reason = failed_items[0].get("reason") if failed_items else "项目同步失败"
                    return BatchDispatchResult(success=False, error=reason, sync_results=sync_results)

            enriched_tasks = enrich_dispatch_tasks(tasks, run_download_info)

            lease_snapshot = await self._revalidate_and_bind(
                enriched_tasks,
                worker,
                require_render=require_render,
                required=require_task_type,
            )

            # 发送批量任务到节点的优先级队列
            result = await self._send_batch_to_queue(
                worker=worker,
                tasks=enriched_tasks,
                batch_id=batch_id or str(uuid.uuid4()),
                lease_snapshot=lease_snapshot,
            )

            return BatchDispatchResult(
                success=result.get("success", False),
                worker_id=worker.public_id,
                worker_name=worker.name,
                batch_id=result.get("batch_id"),
                accepted_count=result.get("accepted_count", 0),
                rejected_count=result.get("rejected_count", 0),
                accepted_tasks=result.get("accepted_tasks", []),
                rejected_tasks=result.get("rejected_tasks", []),
                message=result.get("message", "批量任务已分发"),
                error=result.get("error"),
                sync_results=sync_results,
            )

        except Exception as e:
            return failed_batch_dispatch(worker, e, BatchDispatchResult)

    async def _bind_task_runs_to_worker(
        self,
        tasks: list[dict],
        worker_id: int,
        snapshot: LeaseCapabilitySnapshot,
    ) -> int:
        """P1-FN-03/04: 委托 dispatch_bind_guard(Worker 行锁 + 可派发状态 CAS)。"""
        from antcode_core.application.services.workers.dispatch_authorization import (
            require_task_run_worker_use_access,
        )
        from antcode_core.application.services.workers.dispatch_bind_guard import (
            bind_task_runs_to_worker,
        )

        await require_task_run_worker_use_access(tasks, worker_id)
        return await bind_task_runs_to_worker(tasks, worker_id, snapshot)

    async def _revalidate_and_bind(self, tasks, worker, *, require_render, required) -> LeaseCapabilitySnapshot:
        snapshot = await require_worker_current_requirements(
            worker,
            require_render=require_render,
            required=required,
        )
        await self._bind_task_runs_to_worker(tasks, worker.id, snapshot)
        return snapshot

    async def _ensure_worker_connected(self, worker):
        """确保节点在线（依赖心跳状态）"""
        from antcode_core.application.services.workers.worker_heartbeat_service import (
            WorkerHeartbeatService,
        )

        if worker.status != WorkerStatus.ONLINE:
            return False
        if worker.last_heartbeat is None:
            return False

        timeout = WorkerHeartbeatService.HEARTBEAT_TIMEOUT
        now = datetime.now()
        last_hb = worker.last_heartbeat
        if last_hb.tzinfo is not None:
            last_hb = last_hb.astimezone().replace(tzinfo=None)
        return (now - last_hb).total_seconds() <= timeout

    async def _select_worker(
        self,
        worker_id=None,
        region=None,
        tags=None,
        require_render=False,
        require_task_type: str | frozenset[str] | None = None,
    ):
        """Select an explicit Worker or delegate to constrained AUTO selection."""
        return await select_dispatch_worker(
            self.load_balancer,
            worker_id=worker_id,
            region=region,
            tags=tags,
            require_render=require_render,
            require_task_type=require_task_type,
        )

    # U3 / #16: ready stream 上限,防止 Worker 长时间挂掉时 stream 无限增长。
    # 用 ~10k entries(近似裁剪)。
    #
    # P1-19: 之前 XADD MAXLEN=N 会物理删除超出的历史 entry,与 consumer
    # group ACK 游标无关——Worker 离线积压超过 MAXLEN 时,未 ACK 的老任务
    # 会被静默删除。现改为:
    #   1) XADD 不带 MAXLEN(避免撞未 ACK)
    #   2) XADD 后 XPENDING 拿 group 最小未 ACK msg_id,XTRIM MINID
    #      仅裁剪比它老的(即已全部 ACK 的)entry
    #   3) 若 PEL 为空,退化为 MAXLEN(全量已消费,安全)
    #   4) 未 ACK 数超 ALERT_THRESHOLD 立即告警,提示 Worker 长期离线/卡死
    READY_STREAM_MAXLEN = 10000
    READY_STREAM_PENDING_ALERT_THRESHOLD = 8000

    async def _send_batch_to_queue(self, worker, tasks, batch_id, *, lease_snapshot: LeaseCapabilitySnapshot):
        """Fence the selected Lease generation and publish one ready batch."""
        # E5: consumer group 名带 namespace 前缀，与 worker 侧对齐
        from antcode_core.infrastructure.redis.control_plane import (
            worker_consumer_group,
        )
        from antcode_core.infrastructure.redis.stream_client import StreamClient

        stream = StreamClient()
        stream_key = task_ready_stream(worker.public_id)
        group_name = worker_consumer_group()

        # U3: 写入前确保 consumer group 存在,start_id="0" 让起得晚的
        # Worker 也能 deliver 到历史消息。StreamClient.xgroup_create 已
        # 内置 BUSYGROUP 幂等处理。
        try:
            await stream.xgroup_create(
                stream_key,
                group_name=group_name,
                start_id="0",
                mkstream=True,
            )
        except Exception as exc:
            # 非 BUSYGROUP 类异常(网络抖动等)记一行 warning,不阻塞 XADD
            # —— xadd 自带 reconnect,group 即使本次没建上,Worker 端 start
            # 时还会 ensure_consumer_group。
            logger.warning(f"ensure_group 失败,继续 XADD: {exc}")

        trace_parent = get_current_trace() or ""

        messages = []
        for task in tasks:
            task_id = task.get("task_id", "")
            messages.append(
                {
                    "task_id": task_id,
                    # P1-FN-02: 确定性 dispatch_id(=run_id)。XADD 用自动 Stream ID,
                    # 响应丢失后靠该字段在 stream 尾部查重确认是否已提交。
                    "dispatch_id": task.get("run_id") or task_id,
                    "run_id": task.get("run_id") or task_id,
                    "project_id": task.get("project_id", ""),
                    "project_type": task.get("project_type", "code"),
                    "priority": task.get("priority") or 0,
                    "params": task.get("params") or {},
                    "environment": task.get("environment") or {},
                    "runtime_env_name": task.get("runtime_env_name") or "",
                    "timeout": task.get("timeout", 3600),
                    # A2: source_bundle 契约（direct 模式 poll 侧读同名字段解出 SourceBundle）
                    "source_bundle_uri": task.get("source_bundle_uri") or "",
                    "source_bundle_sha256": task.get("source_bundle_sha256") or "",
                    "source_bundle_size": task.get("source_bundle_size") or 0,
                    "transfer_method": task.get("transfer_method") or "source_bundle",
                    "source_subdir": task.get("source_subdir") or "",
                    "resolved_revision": task.get("resolved_revision") or "",
                    "entry_point": task.get("entry_point") or "",
                    "trace_parent": trace_parent,
                }
            )

        # B12: 不在 scheduler_dispatch_epoch() 内直接抛错，禁止无 Master 栅栏派发。
        scheduler_epoch = ready_publish.require_scheduler_dispatch_epoch()
        try:
            redis = await stream._get_client()
            await ready_publish.publish_ready_batch(
                redis,
                worker_id=worker.public_id,
                snapshot=lease_snapshot,
                messages=messages,
                scheduler_fencing_token=scheduler_epoch,
            )
        except ready_publish.LeaseDispatchFenceError:
            raise
        except Exception as e:
            logger.exception("任务写入 Redis 失败")
            # P1-FN-02: 服务端已提交但响应丢失时,直接判失败会触发上游创建
            # retry run → 原消息 + 新 run 双执行。先按 dispatch_id 查重确认
            # (分类/查重/兜底语义见 stream_dedup 模块注释)。
            from antcode_core.infrastructure.redis.stream_dedup import confirm_dispatch_committed

            if not await confirm_dispatch_committed(stream, stream_key, messages, error=e):
                return {"success": False, "error": str(e)}
            logger.warning("P1-FN-02: XADD 响应丢失但派发消息已确认提交,按成功处理: stream={}", stream_key)

        # P1-19: 写入成功后按 group 已 ACK 游标做安全裁剪。裁剪失败仅
        # 记 warning——不影响本批任务已经落 Redis 的语义。
        try:
            await self._trim_ready_stream(stream, stream_key, group_name)
        except Exception as exc:
            logger.warning("ready stream 裁剪失败 stream={} err={}", stream_key, exc)

        accepted_tasks = [{"task_id": task.get("task_id")} for task in tasks]
        return {
            "success": True,
            "batch_id": batch_id,
            "accepted_count": len(accepted_tasks),
            "rejected_count": 0,
            "accepted_tasks": accepted_tasks,
            "rejected_tasks": [],
            "message": "批量任务已写入 Redis 队列",
        }

    async def _trim_ready_stream(self, stream, stream_key: str, group_name: str) -> None:
        """Trim only below the group's proven delivery or pending boundary."""
        try:
            pending_info = await stream.xpending(stream_key, group_name=group_name)
        except Exception as exc:
            logger.warning(
                "xpending 失败，跳过 ready stream 裁剪 stream={} err={}",
                stream_key,
                exc,
            )
            return

        pending_count = int(pending_info.get("pending_count", 0))
        if pending_count >= self.READY_STREAM_PENDING_ALERT_THRESHOLD:
            logger.error(
                "ready stream 积压逼近上限(可能 Worker 离线/卡死): stream={} pending={} threshold={} maxlen={}",
                stream_key,
                pending_count,
                self.READY_STREAM_PENDING_ALERT_THRESHOLD,
                self.READY_STREAM_MAXLEN,
            )
        from antcode_core.infrastructure.redis.stream_retention import (
            trim_acknowledged_stream,
        )

        client = await stream._get_client()
        await trim_acknowledged_stream(client, stream_key, group_name)

    async def update_task_priority(self, worker, task_id, priority):
        """更新节点上任务的优先级"""
        logger.warning("当前架构不支持更新节点队列任务优先级")
        return {"success": False, "error": "当前架构暂不支持该操作"}

    async def get_queue_status(self, worker):
        """获取节点队列状态（来自心跳指标）"""
        metrics = worker.metrics if isinstance(worker.metrics, dict) else {}
        return {
            "queued_tasks": metrics.get("queuedTasks") or metrics.get("queued_tasks", 0),
            "running_tasks": metrics.get("runningTasks") or metrics.get("running_tasks", 0),
            "max_concurrent_tasks": metrics.get("maxConcurrentTasks") or metrics.get("max_concurrent_tasks", 0),
        }

    async def cancel_queued_task(self, worker, task_id):
        """取消节点队列中的任务"""
        logger.warning("当前架构不支持取消节点队列任务")
        return False

    async def get_task_status_from_worker(self, worker, task_id):
        """从节点获取任务状态"""
        try:
            from antcode_core.domain.models.task_run import TaskRun

            task_id_str = str(task_id)
            execution = await TaskRun.get_or_none(run_id=task_id_str)
            if not execution and len(task_id_str) <= 32:
                execution = await TaskRun.get_or_none(public_id=task_id_str)
            if not execution:
                return None

            return {
                "run_id": execution.run_id,
                "status": execution.status,
                "start_time": execution.start_time.isoformat() if execution.start_time else None,
                "end_time": execution.end_time.isoformat() if execution.end_time else None,
                "exit_code": execution.exit_code,
                "error_message": normalize_persisted_error_message(execution.error_message),
            }
        except Exception:
            logger.exception("获取任务状态失败")
            return None

    async def get_task_logs_from_worker(self, worker, task_id, log_type="output", tail=100):
        """从节点获取任务日志"""
        try:
            from antcode_core.application.services.workers.distributed_log_service import (
                distributed_log_service,
            )

            log_type = "stdout" if log_type == "output" else "stderr" if log_type == "error" else log_type
            return await distributed_log_service.get_logs(
                run_id=str(task_id),
                log_type=log_type,
                tail=tail,
            )
        except Exception:
            logger.exception("获取任务日志失败")
            return []


# 全局实例
worker_load_balancer = WorkerLoadBalancer()
worker_task_dispatcher = WorkerTaskDispatcher()
