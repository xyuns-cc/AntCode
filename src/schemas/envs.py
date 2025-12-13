from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field
from src.models.enums import VenvScope


class PythonVersionListResponse(BaseModel):
    versions: List[str]


class InterpreterInfo(BaseModel):
    id: str | None = Field(None, description="解释器公开ID")
    version: str
    install_dir: str
    python_bin: str
    source: str | None = None


class InstallInterpreterRequest(BaseModel):
    version: str = Field(..., description="Python 版本，如 3.11.9")


class VenvStatusResponse(BaseModel):
    project_id: str = Field(description="项目公开ID")
    scope: Optional[VenvScope] = None
    version: Optional[str] = None
    venv_path: Optional[str] = None


class CreateVenvRequest(BaseModel):
    version: str = Field(..., description="Python 版本，如 3.11.9")
    venv_scope: VenvScope = Field(..., description="虚拟环境作用域：shared/private")
    shared_venv_key: Optional[str] = Field(None, description="共享环境标识（可选），默认用版本号")
    create_if_missing: bool = Field(True, description="不存在则创建（shared 时允许创建共享环境）")
    interpreter_source: Optional[str] = Field("mise", description="解释器来源：mise/local")
    python_bin: Optional[str] = Field(None, description="当来源为local时的python路径")


class CreateSharedVenvRequest(BaseModel):
    version: str = Field(..., description="Python 版本，如 3.11.9")
    shared_venv_key: Optional[str] = Field(None, description="共享环境标识（可选），默认用版本号")
    interpreter_source: Optional[str] = Field("mise", description="解释器来源：mise/local")
    python_bin: Optional[str] = Field(None, description="当来源为local时的python路径")


class VenvListItem(BaseModel):
    id: str = Field(description="虚拟环境公开ID")
    scope: VenvScope
    key: Optional[str] = None
    version: str
    venv_path: str
    interpreter_version: str
    interpreter_source: Optional[str] = None
    python_bin: str
    install_dir: str
    created_by: Optional[str] = Field(None, description="创建者公开ID")
    created_by_username: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    packages: Optional[list[dict]] = None
    current_project_id: Optional[str] = Field(None, description="当前项目公开ID")
