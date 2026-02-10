"""用户管理接口"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from tortoise.exceptions import IntegrityError

from antcode_web_api.response import Messages, success
from antcode_web_api.response import page as page_response
from antcode_core.common.security.auth import (
    TokenData,
    get_current_admin_user,
    get_current_super_admin,
    get_current_user,
    verify_super_admin,
)
from antcode_core.domain.models.audit_log import AuditAction
from antcode_core.domain.models import User
from antcode_core.domain.schemas import (
    BaseResponse,
    PaginationResponse,
    UserAdminPasswordUpdateRequest,
    UserCreateRequest,
    UserPasswordUpdateRequest,
    UserResponse,
    UserUpdateRequest,
)
from antcode_core.domain.schemas.common import PaginationInfo
from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.users.user_service import user_service

router = APIRouter()


def _build_user_response(user) -> UserResponse:
    """构建用户响应"""
    is_online = False
    if user.last_login_at:
        now = datetime.now(user.last_login_at.tzinfo) if user.last_login_at.tzinfo else datetime.now()
        is_online = (now - user.last_login_at).total_seconds() <= user_service.ONLINE_WINDOW_SECONDS

    return UserResponse(
        id=user.public_id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
        is_online=is_online,
    )


@router.get(
    "/",
    response_model=PaginationResponse,
    summary="获取用户列表",
    description="获取所有用户列表（仅管理员）",
    tags=["用户管理"],
)
async def get_users_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    is_active: bool = Query(None),
    is_admin: bool = Query(None),
    sort_by: str = Query(None),
    sort_order: str = Query(None),
    current_admin=Depends(get_current_admin_user),
):
    """获取用户列表（仅管理员可访问，带缓存优化）"""

    try:
        result = await user_service.get_users_list(
            page=page,
            size=size,
            is_active=is_active,
            is_admin=is_admin,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        pag = result["pagination"]
        if isinstance(pag, dict):
            pag = PaginationInfo(**pag)
        items = result["data"]["items"]
        return page_response(
            items=items,
            total=pag.total,
            page=pag.page,
            size=pag.size,
            message=Messages.QUERY_SUCCESS,
        )

    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户列表失败: {str(e)}",
        )


@router.get(
    "/simple",
    response_model=BaseResponse,
    summary="获取用户简易列表",
    description="获取所有用户的简易信息列表（仅管理员）",
    tags=["用户管理"],
)
async def get_simple_user_list(current_admin=Depends(get_current_admin_user)):
    """获取用户简易信息列表"""

    try:
        users = await user_service.get_simple_user_list()
        return success(users, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取用户简易列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户简易列表失败: {str(e)}",
        )


@router.post(
    "/",
    response_model=BaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建用户",
    tags=["用户管理"],
)
async def create_user(
    request: UserCreateRequest,
    http_request: Request,
    current_admin=Depends(get_current_admin_user),
):
    """创建用户（仅管理员/超级管理员）"""
    if request.is_admin and not await verify_super_admin(current_admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有超级管理员可以创建管理员")
    admin_user = await user_service.get_user_by_id(current_admin.user_id)
    try:
        user = await user_service.create_user(request)
        # 记录审计日志
        await audit_service.log_user_action(
            action=AuditAction.USER_CREATE,
            operator_username=admin_user.username,
            target_user_id=user.id,
            target_username=user.username,
            operator_id=admin_user.id,
            ip_address=http_request.client.host if http_request.client else None,
            new_value={
                "username": user.username,
                "email": user.email,
                "is_admin": user.is_admin,
            },
            description=f"创建用户: {user.username}",
        )
        return success(_build_user_response(user), message=Messages.CREATED_SUCCESS, code=201)
    except IntegrityError as e:
        err_msg = str(e)
        if "用户名" in err_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
        if "邮箱" in err_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err_msg)


@router.get("/{user_id}", response_model=BaseResponse, summary="获取用户详情", tags=["用户管理"])
async def get_user_detail(user_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取用户详情"""
    user = await user_service.get_user_by_public_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    # 权限检查
    current_user_obj = await user_service.get_user_by_id(current_user.user_id)
    if not current_user_obj.is_admin and current_user_obj.id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    return success(_build_user_response(user), message=Messages.QUERY_SUCCESS)


@router.put("/{user_id}", response_model=BaseResponse, summary="更新用户信息", tags=["用户管理"])
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    http_request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    """更新用户信息"""
    current_user_obj = await user_service.get_user_by_id(current_user.user_id)
    if not current_user_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前用户不存在")

    target_user = await user_service.get_user_by_public_id(user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    if not current_user_obj.is_admin and current_user_obj.id != target_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    if request.is_admin is not None and not await verify_super_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有超级管理员可以修改管理员权限")
    if request.new_password and current_user_obj.id != target_user.id and not current_user_obj.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权修改其他用户密码")
    if request.new_password and current_user_obj.id == target_user.id and not request.old_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="修改自己的密码必须提供当前密码")

    old_snapshot = {
        "username": target_user.username,
        "email": target_user.email,
        "is_active": target_user.is_active,
        "is_admin": target_user.is_admin,
    }

    try:
        user = await user_service.update_user(user_id, request)

        new_snapshot = {
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "password_updated": bool(request.new_password),
        }

        username_changed = old_snapshot["username"] != new_snapshot["username"]
        operator_username = user.username if current_user_obj.id == user.id else current_user_obj.username

        description = f"更新用户信息: {old_snapshot['username']}"
        if username_changed:
            description = (
                f"更新用户信息: {old_snapshot['username']} -> {new_snapshot['username']}"
            )

        await audit_service.log_user_action(
            action=AuditAction.USER_UPDATE,
            operator_username=operator_username,
            target_user_id=user.id,
            target_username=user.username,
            operator_id=current_user_obj.id,
            ip_address=http_request.client.host if http_request.client else None,
            old_value=old_snapshot,
            new_value=new_snapshot,
            description=description,
        )

        return success(_build_user_response(user), message=Messages.UPDATED_SUCCESS)
    except IntegrityError as e:
        detail = str(e)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在" if "用户名" in detail else ("邮箱已存在" if "邮箱" in detail else detail),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{user_id}/password",
    response_model=BaseResponse,
    summary="修改用户密码",
    tags=["用户管理"],
)
async def update_user_password(
    user_id: str,
    request: UserPasswordUpdateRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """修改用户密码"""
    try:
        await user_service.update_user_password(user_id, request, current_user.user_id)
        return success(None, message="密码修改成功")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/change-password",
    response_model=BaseResponse,
    summary="修改当前用户密码",
    tags=["用户管理"],
)
async def change_password(
    request: UserPasswordUpdateRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """修改当前用户密码"""
    try:
        await user_service.update_user_password(
            current_user.user_id, request, current_user.user_id
        )
        return success(None, message="密码修改成功")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{user_id}/reset-password",
    response_model=BaseResponse,
    summary="重置用户密码",
    tags=["用户管理"],
)
async def reset_user_password(
    user_id: str,
    request: UserAdminPasswordUpdateRequest,
    current_admin: TokenData = Depends(get_current_super_admin),
):
    """重置用户密码（仅超级管理员）"""
    await user_service.reset_user_password(user_id, request.new_password)
    return success(None, message="密码重置成功")


@router.post(
    "/batch/status",
    response_model=BaseResponse,
    summary="批量更新用户状态",
    tags=["用户管理"],
)
async def batch_update_status(
    request: dict,
    current_admin: TokenData = Depends(get_current_admin_user),
):
    user_ids = request.get("user_ids", [])
    is_active = request.get("is_active")
    if not user_ids or is_active is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="参数不完整")

    updated = await User.filter(public_id__in=user_ids).update(is_active=bool(is_active))
    return success({"updated": updated}, message=Messages.UPDATED_SUCCESS)


@router.post(
    "/batch/delete",
    response_model=BaseResponse,
    summary="批量删除用户",
    tags=["用户管理"],
)
async def batch_delete_users(
    request: dict,
    current_admin: TokenData = Depends(get_current_super_admin),
):
    user_ids = request.get("user_ids", [])
    if not user_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户ID列表不能为空")

    success_count = 0
    failed_ids = []
    for user_id in user_ids:
        try:
            await user_service.delete_user(user_id, current_admin.user_id)
            success_count += 1
        except Exception:
            failed_ids.append(user_id)

    return success(
        {"success_count": success_count, "failed_ids": failed_ids},
        message=Messages.DELETED_SUCCESS,
    )

@router.delete("/{user_id}", response_model=BaseResponse, summary="删除用户", tags=["用户管理"])
async def delete_user(
    user_id: str,
    http_request: Request,
    current_admin: TokenData = Depends(get_current_super_admin),
):
    """删除用户（仅超级管理员）"""
    admin_user = await user_service.get_user_by_id(current_admin.user_id)
    target_user = await user_service.get_user_by_public_id(user_id)
    try:
        await user_service.delete_user(user_id, current_admin.user_id)
        # 记录审计日志
        if target_user:
            await audit_service.log_user_action(
                action=AuditAction.USER_DELETE,
                operator_username=admin_user.username,
                target_user_id=target_user.id,
                target_username=target_user.username,
                operator_id=admin_user.id,
                ip_address=http_request.client.host if http_request.client else None,
                description=f"删除用户: {target_user.username}",
            )
        return success(None, message=Messages.DELETED_SUCCESS)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/cache/info",
    response_model=BaseResponse,
    summary="获取缓存信息",
    tags=["用户管理"],
)
async def get_user_list_cache_info(current_admin=Depends(get_current_admin_user)):
    """获取用户列表缓存信息（仅管理员）"""
    cache_info = await user_service.get_cache_info()
    return success(cache_info, message=Messages.QUERY_SUCCESS)


@router.delete("/cache", response_model=BaseResponse, summary="清除缓存", tags=["用户管理"])
async def clear_user_list_cache(current_admin=Depends(get_current_admin_user)):
    """清除用户列表缓存（仅管理员）"""
    await user_service.clear_cache()
    return success(None, message="缓存已清除")
