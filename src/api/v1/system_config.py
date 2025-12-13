"""系统配置管理接口"""
from fastapi import APIRouter, HTTPException, status, Depends
from loguru import logger

from src.core.security.auth import get_current_super_admin
from src.schemas import BaseResponse
from src.core.response import success, Messages
from src.schemas.system_config import (
    SystemConfigCreate,
    SystemConfigUpdate,
    SystemConfigResponse,
    SystemConfigBatchUpdate,
)
from src.services.system_config import system_config_service

router = APIRouter()


@router.get(
    "/",
    response_model=BaseResponse,
    summary="获取所有系统配置",
    description="获取所有系统配置列表（仅超级管理员）",
    tags=["系统配置"]
)
async def get_all_configs(
    category: str = None,
    current_admin=Depends(get_current_super_admin)
):
    """获取所有系统配置（仅超级管理员可访问）"""

    try:
        configs = await system_config_service.get_all_configs(category)
        config_list = [
            SystemConfigResponse.model_validate(config)
            for config in configs
        ]
        return success(config_list, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取系统配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统配置失败: {str(e)}"
        )


@router.get(
    "/by-category",
    response_model=BaseResponse,
    summary="按分类获取系统配置",
    description="按分类获取所有系统配置（仅超级管理员）",
    tags=["系统配置"]
)
async def get_configs_by_category(
    current_admin=Depends(get_current_super_admin)
):
    """按分类获取系统配置（仅超级管理员可访问）"""

    try:
        all_configs = await system_config_service.get_all_configs_by_category()
        return success(all_configs, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取系统配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统配置失败: {str(e)}"
        )


@router.get(
    "/{config_key}",
    response_model=BaseResponse,
    summary="获取单个系统配置",
    description="根据配置键获取配置（仅超级管理员）",
    tags=["系统配置"]
)
async def get_config(
    config_key: str,
    current_admin=Depends(get_current_super_admin)
):
    """获取单个系统配置（仅超级管理员可访问）"""

    try:
        config = await system_config_service.get_config_by_key(config_key)
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"配置键 {config_key} 不存在"
            )

        config_data = SystemConfigResponse.model_validate(config)
        return success(config_data, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取系统配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统配置失败: {str(e)}"
        )


@router.post(
    "/",
    response_model=BaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建系统配置",
    description="创建新的系统配置（仅超级管理员）",
    tags=["系统配置"]
)
async def create_config(
    config_data: SystemConfigCreate,
    current_admin=Depends(get_current_super_admin)
):
    """创建系统配置（仅超级管理员可访问）"""

    try:
        config = await system_config_service.create_config(
            config_data, 
            modified_by=current_admin.username
        )

        response_data = SystemConfigResponse.model_validate(config)
        return success(response_data, message=Messages.CREATED_SUCCESS, code=201)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"创建系统配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建系统配置失败: {str(e)}"
        )


@router.put(
    "/{config_key}",
    response_model=BaseResponse,
    summary="更新系统配置",
    description="更新指定的系统配置（仅超级管理员）",
    tags=["系统配置"]
)
async def update_config(
    config_key: str,
    config_data: SystemConfigUpdate,
    current_admin=Depends(get_current_super_admin)
):
    """更新系统配置（仅超级管理员可访问）"""

    try:
        config = await system_config_service.update_config(
            config_key, 
            config_data, 
            modified_by=current_admin.username
        )

        response_data = SystemConfigResponse.model_validate(config)
        return success(response_data, message=Messages.UPDATED_SUCCESS)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"更新系统配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新系统配置失败: {str(e)}"
        )


@router.post(
    "/batch",
    response_model=BaseResponse,
    summary="批量更新系统配置",
    description="批量更新系统配置（仅超级管理员）",
    tags=["系统配置"]
)
async def batch_update_configs(
    batch_data: SystemConfigBatchUpdate,
    current_admin=Depends(get_current_super_admin)
):
    """批量更新系统配置（仅超级管理员可访问）"""

    try:
        updated_count = await system_config_service.batch_update_configs(
            batch_data.configs, 
            modified_by=current_admin.username
        )

        return success(
            {"updated_count": updated_count}, 
            message=f"成功更新 {updated_count} 个配置项"
        )

    except Exception as e:
        logger.error(f"批量更新系统配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"批量更新系统配置失败: {str(e)}"
        )


@router.delete(
    "/{config_key}",
    response_model=BaseResponse,
    summary="删除系统配置",
    description="删除指定的系统配置（仅超级管理员）",
    tags=["系统配置"]
)
async def delete_config(
    config_key: str,
    current_admin=Depends(get_current_super_admin)
):
    """删除系统配置（仅超级管理员可访问）"""

    try:
        success_flag = await system_config_service.delete_config(config_key)

        if not success_flag:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"配置键 {config_key} 不存在"
            )

        return success(None, message=Messages.DELETED_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除系统配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除系统配置失败: {str(e)}"
        )


@router.post(
    "/reload",
    response_model=BaseResponse,
    summary="重新加载配置",
    description="重新加载所有配置到缓存（仅超级管理员）",
    tags=["系统配置"]
)
async def reload_configs(
    current_admin=Depends(get_current_super_admin)
):
    """重新加载配置（热加载，仅超级管理员可访问）"""

    try:
        await system_config_service.reload_config_cache()
        return success(None, message="配置已重新加载")

    except Exception as e:
        logger.error(f"重新加载配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"重新加载配置失败: {str(e)}"
        )


@router.post(
    "/initialize",
    response_model=BaseResponse,
    summary="初始化默认配置",
    description="初始化默认系统配置（仅超级管理员）",
    tags=["系统配置"]
)
async def initialize_default_configs(
    current_admin=Depends(get_current_super_admin)
):
    """初始化默认配置（仅超级管理员可访问）"""

    try:
        await system_config_service.initialize_default_configs()
        return success(None, message="默认配置已初始化")

    except Exception as e:
        logger.error(f"初始化默认配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"初始化默认配置失败: {str(e)}"
        )

