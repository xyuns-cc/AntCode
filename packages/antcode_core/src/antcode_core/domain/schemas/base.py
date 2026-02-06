"""基础模式"""


from pydantic import BaseModel


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str
    version: str
    timestamp: str


class AppInfoResponse(BaseModel):
    """应用信息响应"""

    name: str  # 应用名称，也用于侧边栏等空间有限处
    title: str  # 完整标题，用于页脚等
    version: str
    description: str = ""
    copyright_year: str = "2025"
