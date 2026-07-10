"""
Domain Models 模块

数据库模型定义：
- base: 基础模型
- enums: 枚举定义
- user: 用户模型
- project: 项目模型
- project_source: 项目 Git 来源配置
- task: 任务定义模型
- task_run: 任务执行实例模型
- task_log: 任务执行日志模型
- runtime: 运行时环境模型
- worker: Worker 节点模型
- worker_install_key: Worker 安装 Key
- crawl: 爬取批次模型
- monitoring: 监控模型
- audit_log: 审计日志模型
- git_credential: Git 凭证
- git_repository: Git 仓库
- artifact: PostgreSQL source bundle / 产物 blob
- run_source_snapshot: 任务运行的源码快照
- system_config: 系统配置模型
"""

# 基础模型
# Artifact 模型
from antcode_core.domain.models.artifact import (
    SourceArtifact,
    SourceArtifactChunk,
)

# 审计日志模型
from antcode_core.domain.models.audit_log import AuditLog
from antcode_core.domain.models.base import (
    BaseModel,
    SoftDeleteMixin,
    TimestampMixin,
    generate_public_id,
)

# 爬取批次模型
from antcode_core.domain.models.crawl import CrawlBatch, CrawlTaskStatus

# 枚举
from antcode_core.domain.models.enums import (
    # 审计日志相关
    AuditAction,
    # 爬取批次相关
    BatchStatus,
    CallbackType,
    CrawlEngine,
    DispatchStatus,
    ExecutionStrategy,
    PaginationType,
    Priority,
    ProjectStatus,
    # 项目相关
    ProjectType,
    RequestMethod,
    RuleType,
    RuntimeLocation,
    # 运行时环境相关
    RuntimeScope,
    RuntimeStatus,
    ScheduleType,
    # 任务相关
    TaskStatus,
    TaskType,
    # Worker 节点相关
    WorkerStatus,
)
from antcode_core.domain.models.git_credential import GitCredential
from antcode_core.domain.models.git_repository import GitRepository

# 监控模型
from antcode_core.domain.models.monitoring import (
    SpiderMetricsHistory,
    WorkerEvent,
    WorkerPerformanceHistory,
)

# 项目模型
from antcode_core.domain.models.project import (
    Project,
    ProjectCode,
    ProjectFile,
    ProjectRule,
)
from antcode_core.domain.models.project_source import ProjectSource
from antcode_core.domain.models.run_source_snapshot import RunSourceSnapshot

# 运行时环境模型
from antcode_core.domain.models.runtime import (
    ProjectRuntimeBinding,
    Runtime,
)

# 系统配置模型
from antcode_core.domain.models.system_config import SystemConfig

# 任务模型
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_log import TaskLog
from antcode_core.domain.models.task_run import TaskRun

# 用户模型
from antcode_core.domain.models.user import User, UserRole, pwd_context

# Worker 节点模型
from antcode_core.domain.models.worker import (
    UserWorkerPermission,
    Worker,
    WorkerHeartbeat,
)

# Worker 安装 Key 模型
from antcode_core.domain.models.worker_install_key import WorkerInstallKey

__all__ = [
    # 基础模型
    "BaseModel",
    "TimestampMixin",
    "SoftDeleteMixin",
    "generate_public_id",
    # 枚举
    "ProjectType",
    "ProjectStatus",
    "CrawlEngine",
    "PaginationType",
    "RuleType",
    "CallbackType",
    "RequestMethod",
    "TaskStatus",
    "DispatchStatus",
    "RuntimeStatus",
    "TaskType",
    "ScheduleType",
    "ExecutionStrategy",
    "RuntimeScope",
    "RuntimeLocation",
    "WorkerStatus",
    "AuditAction",
    "BatchStatus",
    "Priority",
    # 用户模型
    "User",
    "UserRole",
    "pwd_context",
    # 项目模型
    "Project",
    "ProjectFile",
    "ProjectRule",
    "ProjectCode",
    "ProjectSource",
    # 任务模型
    "Task",
    "TaskLog",
    "TaskRun",
    # 运行时环境模型
    "Runtime",
    "ProjectRuntimeBinding",
    # Worker 节点模型
    "Worker",
    "WorkerHeartbeat",
    "UserWorkerPermission",
    # Worker 安装 Key 模型
    "WorkerInstallKey",
    # 爬取批次模型
    "CrawlBatch",
    "CrawlTaskStatus",
    # 监控模型
    "WorkerPerformanceHistory",
    "SpiderMetricsHistory",
    "WorkerEvent",
    # 审计日志模型
    "AuditLog",
    "GitCredential",
    "GitRepository",
    # Artifact 模型
    "SourceArtifact",
    "SourceArtifactChunk",
    "RunSourceSnapshot",
    # 系统配置模型
    "SystemConfig",
]
