"""品牌配置接口"""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from antcode_core.application.services.system_config import system_config_service
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.system_config import BrandingConfig
from antcode_web_api.response import Messages, success

router = APIRouter()


@router.get(
    "/public",
    response_model=BaseResponse[BrandingConfig],
    summary="获取品牌配置（公开）",
    tags=["基础"],
)
async def get_public_branding_config():
    """公开获取品牌配置（登录页等场景）"""

    try:
        branding = system_config_service.get_branding_config()
        return success(branding, message=Messages.QUERY_SUCCESS)
    except Exception as e:
        logger.error(f"获取品牌配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取品牌配置失败: {str(e)}",
        )
