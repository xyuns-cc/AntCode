"""Worker CRUD handler（P2 从 workers.py 拆出）。_worker_to_response 等依赖由
register_crud_routes 注入以避免循环 import；契约（URL / DI / 返回）与旧实现一致。"""

from __future__ import annotations

from antcode_core.application.services.workers import worker_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import UserRole
from antcode_core.domain.schemas.worker import (
    WorkerCreateRequest,
    WorkerListResponse,
    WorkerResponse,
    WorkerTestConnectionResponse,
    WorkerUpdateRequest,
)
from fastapi import Body, Depends, HTTPException, Query, Request, status

from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1.committed_resource_audit import (
    audit_worker_created,
    audit_worker_deleted,
)
from antcode_web_api.routes.v1.mutation_audit import audit_worker_updated
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids


async def get_workers(
    *,
    page: int,
    size: int,
    status_filter: str | None,
    region: str | None,
    search: str | None,
    current_user: TokenData,
    list_accessible_workers,
    worker_to_response,
):
    workers, total = await list_accessible_workers(
        current_user,
        page=page,
        size=size,
        status_filter=status_filter,
        region=region,
        search=search,
    )
    items = [worker_to_response(worker) for worker in workers]
    return success(WorkerListResponse(items=items, total=total, page=page, size=size))


async def create_worker(
    request: WorkerCreateRequest,
    http_request: Request,
    current_user: TokenData,
    *,
    worker_to_response,
):
    worker = await worker_service.create_worker(request, current_user.user_id)
    await audit_worker_created(http_request, current_user, worker)
    return success(worker_to_response(worker), message="Worker 创建成功")


async def get_worker_credentials(worker_id: str, current_user: TokenData, *, require_worker_access):
    await require_worker_access(worker_id, current_user)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Worker 凭据不可恢复，请使用新的安装 Key 重新注册",
    )


async def disconnect_worker(worker_id: str):
    result = await worker_service.disconnect_worker(worker_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return success({"disconnected": True}, message="Worker 已断开")


async def get_worker(worker_id: str, current_user: TokenData, *, require_worker_access, worker_to_response):
    worker = await require_worker_access(worker_id, current_user)
    return success(worker_to_response(worker))


async def update_worker(
    worker_id: str,
    request: WorkerUpdateRequest,
    *,
    http_request: Request,
    current_user: TokenData,
    worker_to_response,
):
    worker = await worker_service.update_worker(worker_id, request)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    await audit_worker_updated(http_request, current_user, worker, changed_fields=sorted(request.model_fields_set))
    return success(worker_to_response(worker), message="Worker 更新成功")


async def delete_worker(worker_id: str, http_request: Request, current_user: TokenData):
    worker = await worker_service.get_worker_by_id(worker_id)
    worker_name = worker.name if worker else worker_id

    deleted = await worker_service.delete_worker(worker_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    await audit_worker_deleted(
        http_request,
        current_user,
        worker_id=worker_id,
        worker_name=worker_name,
    )
    return success({"deleted": True}, message="Worker 删除成功")


async def batch_delete_workers(request: dict):
    worker_ids = bounded_distinct_ids(request.get("worker_ids"), "worker_ids")
    result = await worker_service.batch_delete_workers(worker_ids)
    if result["failed_count"] == 0:
        message = f"成功删除 {result['success_count']} 个 Worker"
    elif result["success_count"] == 0:
        message = f"删除失败，{result['failed_count']} 个 Worker 删除失败"
    else:
        message = f"部分成功：{result['success_count']} 个删除成功，{result['failed_count']} 个失败"
    return success(result, message=message)


async def test_worker_connection(worker_id: str):
    result = await worker_service.test_connection(worker_id)
    return success(WorkerTestConnectionResponse(**result))


async def refresh_worker_status(worker_id: str, *, worker_to_response):
    worker = await worker_service.refresh_worker_status(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return success(worker_to_response(worker))


def register_crud_routes(router, *, worker_to_response, require_worker_access, list_accessible_workers) -> None:
    admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))

    @router.get(
        "",
        response_model=BaseResponse[WorkerListResponse],
        summary="获取 Worker 列表",
        description="获取所有 Worker 列表，支持分页和过滤",
    )
    async def _get_workers(
        *,
        page: int = Query(1, ge=1, description="页码"),
        size: int = Query(20, ge=1, le=100, description="每页数量"),
        status_filter: str | None = Query(None, alias="status", description="状态过滤"),
        region: str | None = Query(None, description="区域过滤"),
        search: str | None = Query(None, description="搜索关键词"),
        current_user: TokenData = Depends(get_current_user),
    ):
        return await get_workers(
            page=page,
            size=size,
            status_filter=status_filter,
            region=region,
            search=search,
            current_user=current_user,
            list_accessible_workers=list_accessible_workers,
            worker_to_response=worker_to_response,
        )

    @router.post(
        "",
        response_model=BaseResponse[WorkerResponse],
        summary="创建 Worker",
        description="手动创建新的 Worker（不推荐，建议使用安装 Key 注册）",
        dependencies=[admin_dep],
    )
    async def _create_worker(
        request: WorkerCreateRequest,
        http_request: Request,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await create_worker(request, http_request, current_user, worker_to_response=worker_to_response)

    @router.get(
        "/{worker_id}/credentials",
        response_model=BaseResponse[dict],
        summary="获取 Worker 凭证",
        description="Worker 凭证不可恢复，仅在注册响应中返回一次",
        dependencies=[admin_dep],
    )
    async def _get_worker_credentials(
        worker_id: str,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await get_worker_credentials(worker_id, current_user, require_worker_access=require_worker_access)

    @router.post(
        "/{worker_id}/disconnect",
        response_model=BaseResponse[dict],
        summary="断开 Worker",
        description="断开与 Worker 的连接",
        dependencies=[admin_dep],
    )
    async def _disconnect_worker(worker_id: str, current_user: TokenData = Depends(get_current_user)):
        _ = current_user
        return await disconnect_worker(worker_id)

    @router.get(
        "/{worker_id}",
        response_model=BaseResponse[WorkerResponse],
        summary="获取 Worker 详情",
        description="根据ID获取 Worker 详细信息",
    )
    async def _get_worker(worker_id: str, current_user: TokenData = Depends(get_current_user)):
        return await get_worker(
            worker_id,
            current_user,
            require_worker_access=require_worker_access,
            worker_to_response=worker_to_response,
        )

    @router.put(
        "/{worker_id}",
        response_model=BaseResponse[WorkerResponse],
        summary="更新 Worker",
        description="更新 Worker 信息",
        dependencies=[admin_dep],
    )
    async def _update_worker(
        worker_id: str,
        request: WorkerUpdateRequest,
        current_user: TokenData = Depends(get_current_user),
        *,
        http_request: Request,
    ):
        return await update_worker(
            worker_id,
            request,
            http_request=http_request,
            current_user=current_user,
            worker_to_response=worker_to_response,
        )

    @router.delete(
        "/{worker_id}",
        response_model=BaseResponse[dict],
        summary="删除 Worker",
        description="删除指定 Worker",
        dependencies=[admin_dep],
    )
    async def _delete_worker(
        worker_id: str,
        http_request: Request,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await delete_worker(worker_id, http_request, current_user)

    @router.post(
        "/batch-delete",
        response_model=BaseResponse[dict],
        summary="批量删除 Worker",
        description="批量删除多个 Worker",
        dependencies=[admin_dep],
    )
    async def _batch_delete_workers(
        request: dict = Body(...),
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await batch_delete_workers(request)

    @router.post(
        "/{worker_id}/test",
        response_model=BaseResponse[WorkerTestConnectionResponse],
        summary="测试 Worker 连接",
        description="测试与 Worker 的网络连接",
        dependencies=[admin_dep],
    )
    async def _test_worker_connection(worker_id: str, current_user: TokenData = Depends(get_current_user)):
        _ = current_user
        return await test_worker_connection(worker_id)

    @router.post(
        "/{worker_id}/refresh",
        response_model=BaseResponse[WorkerResponse],
        summary="刷新 Worker 状态",
        description="重新检测并更新 Worker 状态",
        dependencies=[admin_dep],
    )
    async def _refresh_worker_status(worker_id: str, current_user: TokenData = Depends(get_current_user)):
        _ = current_user
        return await refresh_worker_status(worker_id, worker_to_response=worker_to_response)


__all__ = [
    "batch_delete_workers",
    "create_worker",
    "delete_worker",
    "disconnect_worker",
    "get_worker",
    "get_worker_credentials",
    "get_workers",
    "refresh_worker_status",
    "register_crud_routes",
    "test_worker_connection",
    "update_worker",
]
