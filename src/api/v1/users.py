"""
用户管理API接口 - 纯Controller层，只处理HTTP请求/响应
"""
from fastapi import APIRouter, HTTPException, status, Depends, Query
from loguru import logger
from tortoise.exceptions import IntegrityError

from src.core.auth import get_current_admin_user, TokenData, get_current_user
from src.schemas import (
    BaseResponse,
    PaginationResponse,
    UserResponse,
    UserCreateRequest,
    UserUpdateRequest,
    UserPasswordUpdateRequest,
    UserAdminPasswordUpdateRequest
)
from src.schemas.common import PaginationInfo
from src.core.response import success, page as page_response
from src.core.messages import Messages
from src.services.users.user_service import user_service

router = APIRouter()


@router.get(
    "/",
    response_model=PaginationResponse,
    summary="获取用户列表",
    description="获取所有用户列表（仅管理员）",
    tags=["用户管理"]
)
async def get_users_list(
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    is_active: bool = Query(None, description="是否激活筛选"),
    is_admin: bool = Query(None, description="是否管理员筛选"),
    current_admin = Depends(get_current_admin_user)
):
    """获取用户列表（仅管理员可访问，带缓存优化）"""
    
    try:
        result = await user_service.get_users_list(
            page=page,
            size=size,
            is_active=is_active,
            is_admin=is_admin
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


@router.post(
    "/",
    response_model=BaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建用户",
    description="创建新用户（仅管理员）",
    tags=["用户管理"]
)
async def create_user(
    request: UserCreateRequest,
    current_admin = Depends(get_current_admin_user)
):
    """创建用户（仅管理员可访问）"""

    try:
        user = await user_service.create_user(request)
        
        response_data = UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            created_at=user.created_at,
            last_login_at=user.last_login_at
        )

        return success(response_data, message=Messages.CREATED_SUCCESS, code=201)
        
    except IntegrityError as e:
        if "用户名已存在" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="用户名已存在"
            )
        elif "邮箱已存在" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="邮箱已存在"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e)
            )
    except Exception as e:
        logger.error(f"创建用户失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="创建用户失败"
        )


@router.get(
    "/{user_id}",
    response_model=BaseResponse,
    summary="获取用户详情",
    description="获取指定用户详情",
    tags=["用户管理"]
)
async def get_user_detail(
    user_id: int,
    current_user = Depends(get_current_user)
):
    """获取用户详情"""
    
    try:
        user = await user_service.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户不存在"
            )

        # 权限检查：管理员可查看所有用户，普通用户只能查看自己
        current_user_obj = await user_service.get_user_by_id(current_user.user_id)
        if not current_user_obj.is_admin and current_user.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足，只能查看自己的信息"
            )

        response_data = UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            created_at=user.created_at,
            last_login_at=user.last_login_at
        )

        return success(response_data, message=Messages.QUERY_SUCCESS)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户详情失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取用户详情失败"
        )


@router.put(
    "/{user_id}",
    response_model=BaseResponse,
    summary="更新用户信息",
    description="更新用户基本信息",
    tags=["用户管理"]
)
async def update_user(
    user_id: int,
    request: UserUpdateRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """更新用户信息（管理员可更新所有用户，普通用户只能更新自己）"""

    try:
        # 权限检查
        current_user_obj = await user_service.get_user_by_id(current_user.user_id)
        if not current_user_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="当前用户不存在"
            )

        if not current_user_obj.is_admin and current_user.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足，只能修改自己的信息"
            )

        user = await user_service.update_user(user_id, request)
        
        response_data = UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            created_at=user.created_at,
            last_login_at=user.last_login_at
        )

        return success(response_data, message=Messages.UPDATED_SUCCESS)
        
    except IntegrityError as e:
        if "邮箱已存在" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="邮箱已存在"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e)
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户信息失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新用户信息失败"
        )


@router.put(
    "/{user_id}/password",
    response_model=BaseResponse,
    summary="修改用户密码",
    description="修改用户密码",
    tags=["用户管理"]
)
async def update_user_password(
    user_id: int,
    request: UserPasswordUpdateRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """修改用户密码（管理员可修改所有用户密码，普通用户只能修改自己的）"""

    try:
        await user_service.update_user_password(user_id, request, current_user.user_id)
        
        return success(None, message="密码修改成功")
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"修改密码失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="修改密码失败"
        )


@router.put(
    "/{user_id}/reset-password",
    response_model=BaseResponse,
    summary="重置用户密码",
    description="管理员重置用户密码",
    tags=["用户管理"]
)
async def reset_user_password(
    user_id: int,
    request: UserAdminPasswordUpdateRequest,
    current_admin = Depends(get_current_admin_user)
):
    """重置用户密码（仅管理员可访问）"""

    try:
        await user_service.reset_user_password(user_id, request.new_password)
        
        return success(None, message="密码重置成功")
        
    except Exception as e:
        logger.error(f"重置密码失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="重置密码失败"
        )


@router.delete(
    "/{user_id}",
    response_model=BaseResponse,
    summary="删除用户",
    description="删除指定用户（仅管理员）",
    tags=["用户管理"]
)
async def delete_user(
    user_id: int,
    current_admin = Depends(get_current_admin_user)
):
    """删除用户（仅管理员可访问）"""

    try:
        await user_service.delete_user(user_id, current_admin.user_id)
        
        return success(None, message=Messages.DELETED_SUCCESS)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"删除用户失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除用户失败"
        )


@router.get(
    "/cache/info",
    response_model=BaseResponse,
    summary="获取用户列表缓存信息",
    description="获取用户列表缓存状态信息（仅管理员）",
    tags=["用户管理"]
)
async def get_user_list_cache_info(
    current_admin = Depends(get_current_admin_user)
):
    """获取用户列表缓存信息（仅管理员可访问）"""
    
    try:
        cache_info = await user_service.get_cache_info()
        
        return success(cache_info, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取用户列表缓存信息失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取缓存信息失败: {str(e)}"
        )


@router.delete(
    "/cache",
    response_model=BaseResponse,
    summary="清除用户列表缓存",
    description="手动清除用户列表缓存（仅管理员）",
    tags=["用户管理"]
)
async def clear_user_list_cache(
    current_admin = Depends(get_current_admin_user)
):
    """清除用户列表缓存（仅管理员可访问）"""
    
    try:
        await user_service.clear_cache()
        
        return success(None, message="用户列表缓存已清除")
    except Exception as e:
        logger.error(f"清除用户列表缓存失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清除缓存失败: {str(e)}"
        )
