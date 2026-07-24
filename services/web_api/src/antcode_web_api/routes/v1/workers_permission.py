"""Worker 权限管理接口 (assign / revoke / users / batch / my-available)。

P2 拆分自 workers.py: 5 个权限相关 handler:
- GET /workers/my/available
- GET /workers/{worker_id}/users
- POST /workers/{worker_id}/assign
- DELETE /workers/{worker_id}/revoke/{user_id}
- POST /workers/batch-assign

_worker_to_response 由 register_permission_routes 时从主 workers.py 注入,
避免循环 import。所有 handler 契约 (URL / DI / 返回结构) 与旧实现一致。
"""

from __future__ import annotations

from antcode_core.application.services.workers import worker_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import User, Worker
from antcode_core.domain.schemas.worker import WorkerListResponse
from fastapi import Body, Depends, HTTPException, status
from tortoise.expressions import Q

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids


async def _require_admin(current_user: TokenData) -> User:
    admin_user = await User.get_or_none(id=current_user.user_id)
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return admin_user


async def _require_worker(worker_id: str) -> Worker:
    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return worker


async def get_my_available_workers(current_user: TokenData, worker_to_response):
    user = await User.get_or_none(id=current_user.user_id)
    is_admin = user.is_admin if user else False
    workers = await worker_service.get_user_workers(user_id=current_user.user_id, is_admin=is_admin)
    items = [worker_to_response(worker) for worker in workers]
    return success(WorkerListResponse(items=items, total=len(items), page=1, size=len(items)))


async def get_worker_users(worker_id: str, current_user: TokenData):
    await _require_admin(current_user)
    worker = await _require_worker(worker_id)
    users = await worker_service.get_worker_users(worker.id)
    return success(users)


async def assign_worker_permission(worker_id: str, request: dict, current_user: TokenData):
    await _require_admin(current_user)
    worker = await _require_worker(worker_id)

    user_id = request.get("user_id")
    permission = request.get("permission", "use")
    note = request.get("note")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户ID不能为空")
    # 支持 public_id 或内部 id
    if isinstance(user_id, str):
        user = await User.filter(public_id=user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        user_id = user.id

    await worker_service.assign_worker_to_user(
        worker_id=worker.id,
        user_id=user_id,
        permission=permission,
        assigned_by=current_user.user_id,
        note=note,
    )
    return success({"assigned": True}, message="权限分配成功")


async def revoke_worker_permission(worker_id: str, user_id: str, current_user: TokenData):
    await _require_admin(current_user)
    worker = await _require_worker(worker_id)

    # 支持 public_id 或内部 id
    try:
        internal_user_id = int(user_id)
    except ValueError:
        user = await User.filter(public_id=user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        internal_user_id = user.id

    revoked = await worker_service.revoke_worker_from_user(worker.id, internal_user_id)
    if revoked:
        return success({"revoked": True}, message="权限撤销成功")
    return success({"revoked": False}, message="该用户没有此 Worker 权限")


async def batch_assign_workers(request: dict, current_user: TokenData):
    await _require_admin(current_user)

    user_id = request.get("user_id")
    worker_ids = bounded_distinct_ids(request.get("worker_ids"), "worker_ids")
    permission = request.get("permission", "use")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户ID不能为空")

    # 支持 public_id 与内部 ID 混合查询, 单次 Worker.filter(Q|Q) 避免 N+1
    int_ids: list[int] = []
    str_ids: list[str] = []
    for wid in worker_ids:
        if isinstance(wid, int) or (isinstance(wid, str) and wid.isdigit()):
            int_ids.append(int(wid) if isinstance(wid, str) else wid)
        else:
            str_ids.append(wid)
    conditions = Q()
    if int_ids:
        conditions |= Q(id__in=int_ids)
    if str_ids:
        conditions |= Q(public_id__in=str_ids)
    workers_matched = await Worker.filter(conditions).only("id").all() if (int_ids or str_ids) else []
    internal_ids = [w.id for w in workers_matched]

    result = await worker_service.batch_assign_workers(
        user_id=user_id,
        worker_ids=internal_ids,
        permission=permission,
        assigned_by=current_user.user_id,
    )
    return success(result, message=f"成功分配 {result['success']} 个 Worker 权限")


def register_permission_routes(router, worker_to_response) -> None:
    @router.get(
        "/my/available",
        response_model=BaseResponse[WorkerListResponse],
        summary="获取我可用的 Worker",
        description="获取当前用户有权限访问的 Worker 列表",
    )
    async def _get_my_available_workers(current_user: TokenData = Depends(get_current_user)):
        return await get_my_available_workers(current_user, worker_to_response)

    @router.get(
        "/{worker_id}/users",
        response_model=BaseResponse[list],
        summary="获取 Worker 授权用户",
        description="获取该 Worker 的授权用户列表（管理员）",
    )
    async def _get_worker_users(worker_id: str, current_user: TokenData = Depends(get_current_user)):
        return await get_worker_users(worker_id, current_user)

    @router.post(
        "/{worker_id}/assign",
        response_model=BaseResponse[dict],
        summary="分配 Worker 权限",
        description="给用户分配 Worker 访问权限（管理员）",
    )
    async def _assign_worker_permission(
        worker_id: str,
        request: dict = Body(...),
        current_user: TokenData = Depends(get_current_user),
    ):
        return await assign_worker_permission(worker_id, request, current_user)

    @router.delete(
        "/{worker_id}/revoke/{user_id}",
        response_model=BaseResponse[dict],
        summary="撤销 Worker 权限",
        description="撤销用户的 Worker 访问权限（管理员）",
    )
    async def _revoke_worker_permission(
        worker_id: str,
        user_id: str,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await revoke_worker_permission(worker_id, user_id, current_user)

    @router.post(
        "/batch-assign",
        response_model=BaseResponse[dict],
        summary="批量分配 Worker 权限",
        description="批量给用户分配多个 Worker 权限（管理员）",
    )
    async def _batch_assign_workers(
        request: dict = Body(...),
        current_user: TokenData = Depends(get_current_user),
    ):
        return await batch_assign_workers(request, current_user)


__all__ = [
    "assign_worker_permission",
    "batch_assign_workers",
    "get_my_available_workers",
    "get_worker_users",
    "register_permission_routes",
    "revoke_worker_permission",
]
