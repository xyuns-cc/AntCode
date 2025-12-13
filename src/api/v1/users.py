"""用户管理接口"""
from fastapi import APIRouter, HTTPException, status, Depends, Query, Request
from loguru import logger
from tortoise.exceptions import IntegrityError

from src.core.security.auth import get_current_admin_user, get_current_user, TokenData
from src.core.response import success, page as page_response, Messages
from src.schemas import (
    BaseResponse, PaginationResponse, UserResponse,
    UserCreateRequest, UserUpdateRequest,
    UserPasswordUpdateRequest, UserAdminPasswordUpdateRequest
)
from src.schemas.common import PaginationInfo
from src.services.users.user_service import user_service
from src.services.audit import audit_service
from src.models.audit_log import AuditAction

router = APIRouter()


def _build_user_response(user) -> UserResponse:
    """构建用户响应"""
    return UserResponse(
        id=user.public_id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        last_login_at=user.last_login_at
    )


@router.get(
    "/",
    response_model=PaginationResponse,
    summary="获取用户列表",
    description="获取所有用户列表（仅管理员）",
    tags=["用户管理"]
)
async def get_users_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    is_active: bool = Query(None),
    is_admin: bool = Query(None),
    sort_by: str = Query(None),
    sort_order: str = Query(None),
    current_admin=Depends(get_current_admin_user)
):
    """获取用户列表（仅管理员可访问，带缓存优化）"""

    try:
        result = await user_service.get_users_list(
            page=page,
            size=size,
            is_active=is_active,
            is_admin=is_admin,
            sort_by=sort_by,
            sort_order=sort_order
        )

        pag = result["pagination"]
        # 兼容缓存命中后被反序列化为dict的情况
        if isinstance(pag, dict):
            pag = PaginationInfo(**pag)
        items = result["data"]["items"]
        return page_response(
            items=items,
            total=pag.total,
            page=pag.page,
            size=pag.size,
            message=Messages.QUERY_SUCCESS
        )

    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户列表失败: {str(e)}"
        )


@router.get(
    "/simple",
    response_model=BaseResponse,
    summary="获取用户简易列表",
    description="获取所有用户的简易信息列表（仅管理员）",
    tags=["用户管理"]
)
async def get_simple_user_list(
    current_admin = Depends(get_current_admin_user)
):
    """获取用户简易信息列表"""

    try:
        users = await user_service.get_simple_user_list()
        return success(users, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取用户简易列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户简易列表失败: {str(e)}"
        )


@router.post(
    "/",
    response_model=BaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建用户",
    tags=["用户管理"]
)
async def create_user(
    request: UserCreateRequest,
    http_request: Request,
    current_admin=Depends(get_current_admin_user)
):
    """创建用户（仅管理员）"""
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
            new_value={"username": user.username, "email": user.email, "is_admin": user.is_admin},
            description=f"创建用户: {user.username}"
        )
        return success(_build_user_response(user), message=Messages.CREATED_SUCCESS, code=201)
    except IntegrityError as e:
        err_msg = str(e)
        if "用户名" in err_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
        if "邮箱" in err_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err_msg)


@router.get(
    "/{user_id}",
    response_model=BaseResponse,
    summary="获取用户详情",
    tags=["用户管理"]
)
async def get_user_detail(
    user_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取用户详情"""
    user = await user_service.get_user_by_public_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    # 权限检查
    current_user_obj = await user_service.get_user_by_id(current_user.user_id)
    if not current_user_obj.is_admin and current_user_obj.id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")

    return success(_build_user_response(user), message=Messages.QUERY_SUCCESS)


@router.put(
    "/{user_id}",
    response_model=BaseResponse,
    summary="更新用户信息",
    tags=["用户管理"]
)
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    current_user: TokenData = Depends(get_current_user)
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

    try:
        user = await user_service.update_user(user_id, request)
        return success(_build_user_response(user), message=Messages.UPDATED_SUCCESS)
    except IntegrityError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在" if "邮箱" in str(e) else str(e))


@router.put(
    "/{user_id}/password",
    response_model=BaseResponse,
    summary="修改用户密码",
    tags=["用户管理"]
)
async def update_user_password(
    user_id: str,
    request: UserPasswordUpdateRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """修改用户密码"""
    try:
        await user_service.update_user_password(user_id, request, current_user.user_id)
        return success(None, message="密码修改成功")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{user_id}/reset-password",
    response_model=BaseResponse,
    summary="重置用户密码",
    tags=["用户管理"]
)
async def reset_user_password(
    user_id: str,
    request: UserAdminPasswordUpdateRequest,
    current_admin: TokenData = Depends(get_current_admin_user)
):
    """重置用户密码（仅管理员）"""
    await user_service.reset_user_password(user_id, request.new_password)
    return success(None, message="密码重置成功")


@router.delete(
    "/{user_id}",
    response_model=BaseResponse,
    summary="删除用户",
    tags=["用户管理"]
)
async def delete_user(
    user_id: str,
    http_request: Request,
    current_admin: TokenData = Depends(get_current_admin_user)
):
    """删除用户（仅管理员）"""
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
                description=f"删除用户: {target_user.username}"
            )
        return success(None, message=Messages.DELETED_SUCCESS)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/cache/info",
    response_model=BaseResponse,
    summary="获取缓存信息",
    tags=["用户管理"]
)
async def get_user_list_cache_info(current_admin=Depends(get_current_admin_user)):
    """获取用户列表缓存信息（仅管理员）"""
    cache_info = await user_service.get_cache_info()
    return success(cache_info, message=Messages.QUERY_SUCCESS)


@router.delete(
    "/cache",
    response_model=BaseResponse,
    summary="清除缓存",
    tags=["用户管理"]
)
async def clear_user_list_cache(current_admin=Depends(get_current_admin_user)):
    """清除用户列表缓存（仅管理员）"""
    await user_service.clear_cache()
    return success(None, message="缓存已清除")
