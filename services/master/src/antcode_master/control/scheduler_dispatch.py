"""Worker resolution and dispatch for a scheduler-owned TaskRun."""

from antcode_core.application.services.scheduler.rule_dispatch_constraints import (
    resolve_rule_dispatch_constraints,
)
from antcode_core.common.exceptions import WorkerUnavailableError
from antcode_core.domain.models.enums import ProjectType
from loguru import logger

from antcode_master.dispatch.selector import execution_resolver


async def dispatch_prepared_run(service, dispatch_context):
    """Claim a prepared run, resolve its Worker, and submit it once."""
    task, project, project_detail, execution, run_id, now, claim = dispatch_context
    try:
        token = execution.scheduler_fencing_token
        if token is None:
            raise RuntimeError(f"scheduler-owned run 缺少 fencing token: run_id={run_id}")
        if not await claim(run_id, int(token), now):
            logger.warning(f"派发前 CAS 失败(run 已被取消/推进)，中止派发: run_id={run_id}")
            await service._log_execution(execution, "WARNING", "派发被中止: 执行已被取消或状态已变更")
            return {"success": False, "aborted": True, "error": "派发前置 CAS 失败: 执行已被取消或状态已变更"}
        await service._log_execution(execution, "INFO", "正在分配执行节点...")
        target_worker, strategy = await _resolve_target_worker(task, project, project_detail)
        await service._log_execution(
            execution,
            "INFO",
            f"执行策略: {strategy}, 目标 Worker: {target_worker.name}",
        )
        if project.type == ProjectType.RULE:
            return await service._execute_rule_task(
                task, project, project_detail, execution, target_worker=target_worker
            )
        return await service._execute_distributed_task(task, project, run_id, execution, target_worker)
    except WorkerUnavailableError as exc:
        await service._log_execution(execution, "ERROR", f"节点不可用: {exc.message}")
        return {"success": False, "error": exc.message}


async def _resolve_target_worker(task, project, project_detail):
    if project.type != ProjectType.RULE:
        return await execution_resolver.resolve_execution_worker(task, project)
    if project_detail is None:
        raise WorkerUnavailableError("规则项目详情不存在")
    serializer = getattr(project_detail, "to_dispatch_dict", None)
    if not callable(serializer):
        raise WorkerUnavailableError("规则项目详情缺少分发序列化接口")
    constraints = resolve_rule_dispatch_constraints(project_detail, serializer())
    return await execution_resolver.resolve_execution_worker(
        task,
        project,
        constraints=constraints,
    )


__all__ = ["dispatch_prepared_run"]
