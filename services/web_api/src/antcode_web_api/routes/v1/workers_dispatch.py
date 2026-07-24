"""Worker 任务分发接口 (dispatch / batch / queue 管理)。

P2 拆分自 workers.py: 5 个 dispatch handler + 3 个 schema + 4 个 helper:
- POST /workers/dispatch/task
- POST /workers/dispatch/batch
- GET /workers/dispatch/queue/{worker_id}/status
- PUT /workers/dispatch/queue/{worker_id}/tasks/{task_id}/priority
- DELETE /workers/dispatch/queue/{worker_id}/tasks/{task_id}

_require_worker_access + _request_user 由 register_dispatch_routes 从主
workers.py 注入 (避免循环 import)。契约 (URL / DI / 参数校验 / 返回) 与
旧实现一致。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, NoReturn

from antcode_core.application.services.workers import worker_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import DispatchStatus, Task, TaskRun, TaskStatus, UserRole
from fastapi import Body, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success

# 参数验证常量
_MAX_DISPATCH_BATCH_TASKS = 500
_MAX_DISPATCH_TIMEOUT_SECONDS = 86400
_PRIORITY_MIN, _PRIORITY_MAX = 0, 4


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    def get(self, key: str, default=None):
        value = getattr(self, key, default)
        return default if value is None else value


class WorkerDispatchTaskRequest(_StrictRequestModel):
    project_id: str = Field(..., min_length=1, max_length=64)
    task_id: int = Field(..., gt=0)
    params: dict = Field(default_factory=dict)
    environment_vars: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=3600, ge=1, le=_MAX_DISPATCH_TIMEOUT_SECONDS)
    worker_id: str | None = Field(default=None, min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=32)
    priority: int | None = Field(default=None, ge=_PRIORITY_MIN, le=_PRIORITY_MAX)
    project_type: str = Field(default="code", pattern="^(code|file|rule)$")
    require_render: bool = False


class WorkerDispatchBatchTaskRequest(_StrictRequestModel):
    project_id: str = Field(..., min_length=1, max_length=64)
    task_id: int = Field(..., gt=0)
    run_id: str = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=3600, ge=1, le=_MAX_DISPATCH_TIMEOUT_SECONDS)
    priority: int | None = Field(default=None, ge=_PRIORITY_MIN, le=_PRIORITY_MAX)
    project_type: str = Field(default="code", pattern="^(code|file|rule)$")
    require_render: bool = False


class WorkerDispatchBatchRequest(_StrictRequestModel):
    tasks: list[WorkerDispatchBatchTaskRequest] = Field(
        ...,
        min_length=1,
        max_length=_MAX_DISPATCH_BATCH_TASKS,
    )
    worker_id: str | None = Field(default=None, min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=32)
    batch_id: str | None = Field(default=None, max_length=128)
    require_render: bool = False


async def _resolve_dispatch_worker(
    requested_worker_id: str | None,
    current_user: TokenData,
    *,
    require_worker_access,
    request_user,
) -> str | None:
    user = await request_user(current_user)
    if user.is_admin:
        return requested_worker_id
    if not requested_worker_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="普通用户分发任务时必须显式指定已授权 Worker",
        )
    worker = await require_worker_access(requested_worker_id, current_user, "use")
    return worker.public_id


async def _finalize_failed_dispatch_run(run_id: str, reason: str) -> None:
    """P1-FN-07 补偿:仍处于 pending 的直接分发 run 收敛为 FAILED。"""
    try:
        await TaskRun.filter(run_id=run_id, status=TaskStatus.PENDING).update(
            status=TaskStatus.FAILED,
            dispatch_status=DispatchStatus.FAILED,
            end_time=datetime.now(UTC),
            error_message=reason[:500],
        )
    except Exception:
        logger.exception("直接分发失败补偿未完成(交由 reconcile 兜底): run_id={}", run_id)


async def _fail_task_dispatch(task_run: TaskRun, run_id: str, error: str | None) -> NoReturn:
    task_run.status = TaskStatus.FAILED
    task_run.dispatch_status = DispatchStatus.FAILED
    task_run.error_message = error or "任务分发失败"
    await task_run.save()
    logger.error("任务分发失败: run_id={} error={}", run_id, error)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="任务分发失败",
    )


async def _get_dispatch_task(project_id: str, task_id_raw, project) -> Task:
    if task_id_raw in (None, "", 0):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "缺少 task_id: 单任务分发必须显式提供真实 task_id, 不接受用 project_id 兜底 (会导致 orphan / 错误归属)"
            ),
        )
    try:
        task_id = int(task_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id 必须是整数") from None

    task = await Task.filter(id=task_id, project_id=project.id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"task_id={task_id} 不存在或不属于 project_id={project_id}",
        )
    return task


async def _build_rule_dispatch_params(request: WorkerDispatchTaskRequest, project) -> dict:
    params = dict(request.get("params") or {})
    if request.get("project_type", "code") != "rule":
        return params
    from antcode_core.domain.models.project import ProjectRule

    rule = await ProjectRule.get_or_none(project_id=project.id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="规则项目缺少 ProjectRule 详情",
        )
    kwargs = dict(params.get("kwargs") or {})
    kwargs["rule_detail"] = rule.to_dispatch_dict()
    params["kwargs"] = kwargs
    return params


async def dispatch_task_to_worker(
    request: WorkerDispatchTaskRequest,
    current_user: TokenData,
    *,
    workers_module,
):
    from antcode_core.application.services.projects.project_service import project_service
    from antcode_core.application.services.workers import worker_task_dispatcher

    project_id = request.get("project_id")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="项目ID不能为空")

    # 从 workers module 每次 lookup, 让测试 monkeypatch.setattr(workers, ...) 生效
    dispatch_worker_id = await workers_module._resolve_dispatch_worker(request.get("worker_id"), current_user)

    # D1: 按 owner 校验解析项目 (admin 放行, 非本人 404 防 IDOR)
    project = await project_service.get_project_by_id(project_id, current_user.user_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    # P1-23: 校验 task 存在且属于该 project
    task_obj = await _get_dispatch_task(project_id, request.get("task_id"), project)

    run_id = str(uuid.uuid4())
    public_id = run_id.replace("-", "")
    task_run = await TaskRun.create(
        run_id=run_id,
        public_id=public_id,
        task_id=task_obj.id,
        project_id=project.id,
        status="pending",
        dispatch_status="pending",
        created_by=current_user.user_id,
        created_at=datetime.now(UTC),
    )

    # P1-FN-07: TaskRun 创建之后的任何校验/分发异常都必须补偿收敛该 run
    try:
        params = await _build_rule_dispatch_params(request, project)
        environment_vars = dict(request.get("environment_vars") or {})
        environment_vars.pop("ANTCODE_RUNTIME_ENV", None)
        runtime_env_name = None
        if getattr(project, "env_location", None) == "worker" and getattr(project, "worker_env_name", None):
            runtime_env_name = project.worker_env_name

        result = await worker_task_dispatcher.dispatch_task(
            project_id=project_id,
            run_id=run_id,
            params=params,
            environment_vars=environment_vars,
            runtime_env_name=runtime_env_name,
            timeout=request.get("timeout", 3600),
            worker_id=dispatch_worker_id,
            region=request.get("region"),
            tags=request.get("tags"),
            priority=request.get("priority"),
            project_type=request.get("project_type", "code"),
            require_render=request.get("require_render", False),
        )
    except HTTPException as exc:
        await _finalize_failed_dispatch_run(run_id, f"分发前校验失败: {exc.detail}")
        raise
    except Exception as exc:
        await _finalize_failed_dispatch_run(run_id, f"分发异常: {exc}")
        raise

    if not result.success:
        await _fail_task_dispatch(task_run, run_id, result.error)

    # 复审 F5-B: 定向 CAS 更新, 避免整行 save() clobber 已终态的 run
    dispatch_updates: dict[str, Any] = {"dispatch_status": DispatchStatus.DISPATCHED}
    if result.worker_id:
        from antcode_core.domain.models import Worker as _W

        _worker = await _W.filter(public_id=result.worker_id).only("id").first()
        if _worker:
            dispatch_updates["worker_id"] = _worker.id
    updated = await TaskRun.filter(
        run_id=run_id,
        dispatch_status=DispatchStatus.PENDING,
    ).update(**dispatch_updates)
    if not updated:
        logger.warning("直接分发状态回写被跳过(run 已被推进/取消): run_id={}", run_id)

    return success(asdict(result), message="任务已分发到 Worker")


async def dispatch_batch_to_worker(
    request: WorkerDispatchBatchRequest,
    current_user: TokenData,
    *,
    workers_module,
):
    from antcode_core.application.services.projects.project_service import project_service
    from antcode_core.application.services.workers import worker_task_dispatcher

    from antcode_web_api.routes.v1.worker_dispatch_guard import authorize_batch_dispatch_tasks

    dispatch_worker_id = await workers_module._resolve_dispatch_worker(request.get("worker_id"), current_user)
    # P1-FN-03: 授权 + 可重派状态校验 (终态/运行中/已取消 run 逐条 409)
    tasks = await authorize_batch_dispatch_tasks(
        request.tasks,
        current_user.user_id,
        project_service,
    )

    result = await worker_task_dispatcher.dispatch_batch(
        tasks=tasks,
        worker_id=dispatch_worker_id,
        region=request.get("region"),
        tags=request.get("tags"),
        batch_id=request.get("batch_id"),
        require_render=request.get("require_render", False),
    )
    if not result.success:
        logger.error("批量任务分发失败: batch_id={} error={}", request.get("batch_id"), result.error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="批量任务分发失败",
        )

    return success(asdict(result), message="批量任务已分发到 Worker")


async def get_worker_queue_status(worker_id: str):
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    status_data = await worker_task_dispatcher.get_queue_status(worker)
    return success(status_data)


async def update_worker_task_priority(worker_id: str, task_id: str, request: dict):
    from antcode_core.application.services.workers import worker_task_dispatcher

    priority = request.get("priority")
    if priority is None or not (_PRIORITY_MIN <= priority <= _PRIORITY_MAX):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"优先级必须是 {_PRIORITY_MIN}-{_PRIORITY_MAX} 之间的整数",
        )
    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    result = await worker_task_dispatcher.update_task_priority(worker, task_id, priority)
    if not result.get("success"):
        error = result.get("error", "")
        if error == "当前架构暂不支持该操作":
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=error)
        if "不存在" in error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="队列任务不存在")
        logger.error(
            "更新 Worker 队列任务优先级失败: worker_id={} task_id={} error={}",
            worker_id,
            task_id,
            error,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新优先级失败",
        )
    return success(result, message="优先级已更新")


async def cancel_worker_queued_task(worker_id: str, task_id: str):
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    success_flag = await worker_task_dispatcher.cancel_queued_task(worker, task_id)
    if not success_flag:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="当前架构暂不支持该操作")
    return success({"task_id": task_id}, message="任务已取消")


def register_dispatch_routes(router, workers_module) -> None:
    """workers_module 是主 workers 模块 (import 传入), handler 内每次 lookup
    workers_module._resolve_dispatch_worker 让测试 monkeypatch 生效。
    """
    admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))

    @router.post(
        "/dispatch/task",
        response_model=BaseResponse[dict],
        summary="分发任务到 Worker",
        description="自动选择最佳 Worker 或指定 Worker 执行任务",
    )
    async def _dispatch_task_to_worker(
        request: WorkerDispatchTaskRequest,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await dispatch_task_to_worker(request, current_user, workers_module=workers_module)

    @router.post(
        "/dispatch/batch",
        response_model=BaseResponse[dict],
        summary="批量分发任务到 Worker",
        description="批量分发多个任务到指定 Worker 的优先级队列",
    )
    async def _dispatch_batch_to_worker(
        request: WorkerDispatchBatchRequest,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await dispatch_batch_to_worker(request, current_user, workers_module=workers_module)

    @router.get(
        "/dispatch/queue/{worker_id}/status",
        response_model=BaseResponse[dict],
        summary="获取 Worker 队列状态",
        description="获取指定 Worker 的优先级队列状态",
        dependencies=[admin_dep],
    )
    async def _get_worker_queue_status(
        worker_id: str,
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await get_worker_queue_status(worker_id)

    @router.put(
        "/dispatch/queue/{worker_id}/tasks/{task_id}/priority",
        response_model=BaseResponse[dict],
        summary="更新任务优先级",
        description="更新 Worker 队列中任务的优先级",
        dependencies=[admin_dep],
    )
    async def _update_worker_task_priority(
        worker_id: str,
        task_id: str,
        *,
        request: dict = Body(...),
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await update_worker_task_priority(worker_id, task_id, request)

    @router.delete(
        "/dispatch/queue/{worker_id}/tasks/{task_id}",
        response_model=BaseResponse[dict],
        summary="取消队列中的任务",
        description="取消 Worker 优先级队列中的任务",
        dependencies=[admin_dep],
    )
    async def _cancel_worker_queued_task(
        worker_id: str,
        task_id: str,
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await cancel_worker_queued_task(worker_id, task_id)


__all__ = [
    "WorkerDispatchBatchRequest",
    "WorkerDispatchBatchTaskRequest",
    "WorkerDispatchTaskRequest",
    "cancel_worker_queued_task",
    "dispatch_batch_to_worker",
    "dispatch_task_to_worker",
    "get_worker_queue_status",
    "register_dispatch_routes",
    "update_worker_task_priority",
]
