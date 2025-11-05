"""
基础功能相关的Pydantic模式定义
包含健康检查等基础功能的数据模式
"""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应模型"""
    status: str = Field(..., description="服务状态")
    version: str = Field(..., description="应用版本")
    timestamp: str = Field(..., description="检查时间")
