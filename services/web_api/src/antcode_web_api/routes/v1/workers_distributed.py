"""Distributed task status / logs 查询接口。

P2 拆分自 workers.py: 3 个从远程 Worker / distributed_log_service 拉取任务
状态和日志的 handler:
- GET /workers/dispatch/task/{worker_id}/{task_id}/status
- GET /workers/dispatch/task/{worker_id}/{task_id}/logs
- GET /workers/distributed-logs/{run_id}

_require_worker_access + _require_run_access 由 register_distributed_routes
时从主 workers.py 注入,避免循环 import。
"""

from __future__ import annotations

from antcode_core.common.security.auth import TokenData, get_current_user
from fastapi import Depends, HTTPException, Query, status

from antcode_web_api.response import BaseResponse, success


async def get_distributed_task_status(
    worker_id: str,
    task_id: str,
    current_user,
    *,
    require_worker_access,
    require_run_access,
):
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await require_worker_access(worker_id, current_user)
    await require_run_access(task_id, current_user)

    status_data = await worker_task_dispatcher.get_task_status_from_worker(worker, task_id)
    if not status_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在或无法获取状态")
    return success(status_data)


async def get_distributed_task_logs(
    worker_id: str,
    task_id: str,
    log_type: str,
    *,
    tail: int,
    current_user,
    require_worker_access,
    require_run_access,
):
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await require_worker_access(worker_id, current_user)
    await require_run_access(task_id, current_user)

    logs = await worker_task_dispatcher.get_task_logs_from_worker(worker, task_id, log_type, tail)
    return success({"logs": logs, "total": len(logs), "worker_id": worker_id, "task_id": task_id})


async def get_distributed_logs(
    run_id: str,
    log_type: str,
    *,
    tail: int,
    current_user,
    require_run_access,
):
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service

    await require_run_access(run_id, current_user)

    logs = await distributed_log_service.get_logs(run_id, log_type=log_type, tail=tail)
    return success(
        {
            "run_id": run_id,
            "log_type": log_type,
            "logs": logs,
            "total": len(logs),
        }
    )


def register_distributed_routes(router, require_worker_access, require_run_access) -> None:
    @router.get(
        "/dispatch/task/{worker_id}/{task_id}/status",
        response_model=BaseResponse[dict],
        summary="获取分布式任务状态",
        description="从指定 Worker 获取任务执行状态",
    )
    async def _get_distributed_task_status(
        worker_id: str,
        task_id: str,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await get_distributed_task_status(
            worker_id,
            task_id,
            current_user,
            require_worker_access=require_worker_access,
            require_run_access=require_run_access,
        )

    @router.get(
        "/dispatch/task/{worker_id}/{task_id}/logs",
        response_model=BaseResponse[dict],
        summary="获取分布式任务日志",
        description="从指定 Worker 获取任务执行日志",
    )
    async def _get_distributed_task_logs(
        worker_id: str,
        task_id: str,
        *,
        log_type: str = Query("output", description="日志类型: output/error"),
        tail: int = Query(100, ge=1, le=1000, description="返回最后N行"),
        current_user: TokenData = Depends(get_current_user),
    ):
        return await get_distributed_task_logs(
            worker_id,
            task_id,
            log_type,
            tail=tail,
            current_user=current_user,
            require_worker_access=require_worker_access,
            require_run_access=require_run_access,
        )

    @router.get(
        "/distributed-logs/{run_id}",
        response_model=BaseResponse[dict],
        summary="获取分布式任务日志(run_id 入口)",
        description="获取在远程 Worker 执行的任务日志",
    )
    async def _get_distributed_logs(
        run_id: str,
        *,
        log_type: str = Query("stdout", description="日志类型: stdout/stderr"),
        tail: int = Query(100, ge=1, le=5000, description="返回最后N行"),
        current_user: TokenData = Depends(get_current_user),
    ):
        return await get_distributed_logs(
            run_id, log_type, tail=tail, current_user=current_user, require_run_access=require_run_access
        )


__all__ = [
    "get_distributed_logs",
    "get_distributed_task_logs",
    "get_distributed_task_status",
    "register_distributed_routes",
]
