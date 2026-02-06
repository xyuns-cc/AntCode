"""
Domain Schemas 模块

Pydantic Schema 定义：
- common: 通用 Schema
- user: 用户 Schema
- task: 任务 Schema
- runtime: 运行时环境 Schema
- worker: Worker 节点 Schema
"""

# 通用 Schema
from antcode_core.domain.schemas.common import (
    AppInfoResponse,
    BaseResponse,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    PaginationInfo,
    PaginationParams,
    PaginationResponse,
)

# 运行时环境 Schema
from antcode_core.domain.schemas.runtime import (
    CreateRuntimeRequest,
    CreateSharedRuntimeRequest,
    InstallInterpreterRequest,
    InterpreterInfo,
    PythonVersionListResponse,
    RuntimeListItem,
    RuntimeStatusResponse,
)

# 任务 Schema
from antcode_core.domain.schemas.task import (
    SystemMetricsResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskRunListResponse,
    TaskRunResponse,
    TaskStatsResponse,
    TaskUpdateRequest,
)

# 用户 Schema
from antcode_core.domain.schemas.user import (
    LoginPublicKeyResponse,
    UserAdminPasswordUpdateRequest,
    UserCreateRequest,
    UserListResponse,
    UserLoginRequest,
    UserLoginResponse,
    UserPasswordUpdateRequest,
    UserResponse,
    UserSimpleResponse,
    UserUpdateRequest,
)

# Worker Schema
from antcode_core.domain.schemas.worker import (
    WorkerAggregateStats,
    WorkerCapabilities,
    WorkerCreateRequest,
    WorkerCredentialsResponse,
    WorkerHeartbeatRequest,
    WorkerListResponse,
    WorkerMetrics,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
    WorkerRegisterDirectRequest,
    WorkerRegisterDirectResponse,
    WorkerResponse,
    WorkerTestConnectionResponse,
    WorkerUpdateRequest,
)

__all__ = [
    # 通用 Schema
    "BaseResponse",
    "ErrorDetail",
    "ErrorResponse",
    "PaginationParams",
    "PaginationInfo",
    "PaginationResponse",
    "HealthResponse",
    "AppInfoResponse",
    # 用户 Schema
    "UserLoginRequest",
    "UserCreateRequest",
    "UserUpdateRequest",
    "UserPasswordUpdateRequest",
    "UserAdminPasswordUpdateRequest",
    "UserResponse",
    "UserSimpleResponse",
    "UserListResponse",
    "UserLoginResponse",
    "LoginPublicKeyResponse",
    # 任务 Schema
    "TaskCreateRequest",
    "TaskUpdateRequest",
    "TaskResponse",
    "TaskListResponse",
    "TaskRunResponse",
    "TaskRunListResponse",
    "TaskStatsResponse",
    "SystemMetricsResponse",
    # 运行时环境 Schema
    "PythonVersionListResponse",
    "InterpreterInfo",
    "InstallInterpreterRequest",
    "RuntimeStatusResponse",
    "CreateRuntimeRequest",
    "CreateSharedRuntimeRequest",
    "RuntimeListItem",
    # Worker Schema
    "WorkerCapabilities",
    "WorkerMetrics",
    "WorkerCreateRequest",
    "WorkerUpdateRequest",
    "WorkerResponse",
    "WorkerListResponse",
    "WorkerAggregateStats",
    "WorkerHeartbeatRequest",
    "WorkerTestConnectionResponse",
    "WorkerRegisterRequest",
    "WorkerRegisterResponse",
    "WorkerRegisterDirectRequest",
    "WorkerRegisterDirectResponse",
    "WorkerCredentialsResponse",
]
