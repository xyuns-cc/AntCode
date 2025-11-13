"""
Pydantic模式定义模块
包含所有API请求和响应的数据模式
"""

from .base import (
    HealthResponse,
)
from .common import (
    BaseResponse,
    ErrorResponse,
    PaginationParams,
    PaginationResponse,
)
from .project import (
    ProjectCreateRequest,
    ProjectFileCreateRequest,
    ProjectRuleCreateRequest,
    ProjectCodeCreateRequest,
    ProjectResponse,
    ProjectListResponse,
    ProjectUpdateRequest,
    ProjectCreateFormRequest,
    ProjectListQueryRequest,
    # 新增任务JSON相关模型
    ExtractionRule,
    PaginationConfig,
    TaskMeta,
    TaskJsonRequest,
    ProjectRuleUpdateRequest,
    ProjectFileContentUpdateRequest,
)
from .user import (
    UserLoginRequest,
    UserCreateRequest,
    UserUpdateRequest,
    UserPasswordUpdateRequest,
    UserAdminPasswordUpdateRequest,
    UserResponse,
    UserSimpleResponse,
    UserListResponse,
    UserLoginResponse,
)

__all__ = [
    # 基础功能
    "HealthResponse",
    # 用户相关
    "UserLoginRequest",
    "UserCreateRequest",
    "UserUpdateRequest",
    "UserPasswordUpdateRequest",
    "UserAdminPasswordUpdateRequest",
    "UserResponse",
    "UserSimpleResponse",
    "UserListResponse",
    "UserLoginResponse",
    # 项目相关
    "ProjectCreateRequest",
    "ProjectFileCreateRequest",
    "ProjectRuleCreateRequest",
    "ProjectCodeCreateRequest",
    "ProjectResponse",
    "ProjectListResponse",
    "ProjectUpdateRequest",
    "ProjectCreateFormRequest",
    "ProjectListQueryRequest",
    # 任务JSON相关
    "ExtractionRule",
    "PaginationConfig",
    "TaskMeta",
    "TaskJsonRequest",
    "ProjectRuleUpdateRequest",
    "ProjectFileContentUpdateRequest",
    # 通用响应
    "BaseResponse",
    "ErrorResponse",
    "PaginationParams",
    "PaginationResponse",
]
