"""请求/响应模型"""

from typing import Optional, List
from pydantic import BaseModel, Field


# ============ 节点相关 ============

class ConnectRequest(BaseModel):
    machine_code: str
    api_key: str
    master_url: str
    node_id: Optional[str] = None  # 节点ID，用于心跳上报


class ConnectRequestV2(BaseModel):
    machine_code: str
    api_key: str
    master_url: str
    node_id: Optional[str] = None
    secret_key: Optional[str] = None
    use_websocket: bool = True


class DisconnectRequest(BaseModel):
    machine_code: str


# ============ 环境相关 ============

class CreateEnvRequest(BaseModel):
    name: str
    python_version: Optional[str] = None
    packages: Optional[List[str]] = None
    created_by: Optional[str] = Field(None, description="创建人用户名")


class UpdateEnvRequest(BaseModel):
    key: Optional[str] = Field(None, description="环境标识")
    description: Optional[str] = Field(None, description="环境描述")


class InstallPackagesRequest(BaseModel):
    packages: List[str]
    upgrade: bool = False


class UninstallPackagesRequest(BaseModel):
    packages: List[str]


# ============ 项目相关 ============

class CreateCodeProjectRequest(BaseModel):
    name: str
    code_content: str
    language: str = "python"
    description: str = ""
    entry_point: Optional[str] = None
    env_name: Optional[str] = None
    master_project_id: Optional[str] = None


class SyncFromMasterRequest(BaseModel):
    """从主节点同步项目请求"""
    project_id: str = Field(..., description="主节点项目ID")
    name: str = Field(..., description="项目名称")
    download_url: str = Field(..., description="下载URL")
    description: str = Field("", description="项目描述")
    entry_point: Optional[str] = Field(None, description="入口文件")
    transfer_method: str = Field("original", description="传输方式")
    file_hash: Optional[str] = Field(None, description="文件哈希")
    file_size: Optional[int] = Field(None, description="文件大小")
    api_key: Optional[str] = Field(None, description="下载认证密钥")


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    entry_point: Optional[str] = None
    env_name: Optional[str] = None
    code_content: Optional[str] = None


class WriteFileRequest(BaseModel):
    file_path: str
    content: str


# ============ 任务相关 ============

class CreateTaskRequest(BaseModel):
    project_id: str
    params: Optional[dict] = None
    environment_vars: Optional[dict] = None
    timeout: int = 3600
