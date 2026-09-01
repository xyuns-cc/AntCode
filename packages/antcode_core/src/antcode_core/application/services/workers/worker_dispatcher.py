"""节点任务分发的编排：从一批任务走到"已写进目标 Worker 的 ready 队列"。

本模块只负责把这几步串起来并把结果翻译成调用方的形状；每一步各自的判据都在专门的模块里：
选节点与准入 ``worker_dispatch_admission``、代码送达 ``dispatch_source_bundle_plan``、
能力复核与 run 绑定 ``worker_capability_routing`` / ``dispatch_bind_guard``、
真正的队列写入 ``worker_ready_stream``。
"""

import uuid
from dataclasses import dataclass, field

from loguru import logger

from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.workers.dispatch_source_bundle_plan import prepare_source_bundles
from antcode_core.application.services.workers.dispatch_task_enrichment import (
    RUNTIME_ENV_KEY,
    enrich_dispatch_tasks,
)
from antcode_core.application.services.workers.worker_capability_routing import (
    require_worker_current_requirements,
    required_execution_task_types,
)
from antcode_core.application.services.workers.worker_dispatch_admission import admit_dispatch_worker
from antcode_core.application.services.workers.worker_dispatch_failure import failed_batch_dispatch
from antcode_core.application.services.workers.worker_load_balancing import WorkerLoadBalancer
from antcode_core.application.services.workers.worker_ready_stream import publish_ready_batch_to_worker


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
        """分发单个任务到节点（走批量接口，再把结果翻回单任务形状）"""
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
        if not result.success:
            return _failed_single_dispatch(result)
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

    async def dispatch_batch(
        self,
        tasks,
        worker_id=None,
        region=None,
        tags=None,
        batch_id=None,
        require_render=False,
    ):
        """批量分发任务到节点（写入 Worker 的 ready stream）"""
        if not tasks:
            return BatchDispatchResult(success=False, error="任务列表为空")

        require_render = require_render or any(task.get("require_render") for task in tasks)
        require_task_type = required_execution_task_types(tasks)

        admission = await admit_dispatch_worker(
            self.load_balancer,
            worker_id=worker_id,
            region=region,
            tags=tags,
            require_render=require_render,
            require_task_type=require_task_type,
        )
        if admission.worker is None:
            return BatchDispatchResult(success=False, error=admission.error)

        try:
            return await self._publish_batch(
                admission.worker,
                tasks,
                batch_id=batch_id,
                require_render=require_render,
                require_task_type=require_task_type,
            )
        except Exception as e:
            return failed_batch_dispatch(admission.worker, e, BatchDispatchResult)

    async def _publish_batch(self, worker, tasks, *, batch_id, require_render, require_task_type):
        """准入之后的固定次序：备好代码 → 复核能力并绑定 run → 写队列。"""
        plan = await prepare_source_bundles(worker, tasks)
        if plan.failure_reason:
            return BatchDispatchResult(success=False, error=plan.failure_reason, sync_results=plan.sync_results)

        enriched_tasks = enrich_dispatch_tasks(tasks, plan.run_download_info)
        lease_snapshot = await self._revalidate_and_bind(
            enriched_tasks,
            worker,
            require_render=require_render,
            required=require_task_type,
        )
        result = await publish_ready_batch_to_worker(
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
            sync_results=plan.sync_results,
        )

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


def _failed_single_dispatch(result: BatchDispatchResult) -> DispatchResult:
    error_msg = result.error
    if not error_msg and result.rejected_tasks:
        error_msg = result.rejected_tasks[0].get("reason", "任务被拒绝")
    return DispatchResult(
        success=False,
        error=error_msg or "任务分发失败，未知原因",
        worker_id=result.worker_id,
        worker_name=result.worker_name,
    )


worker_task_dispatcher = WorkerTaskDispatcher()
