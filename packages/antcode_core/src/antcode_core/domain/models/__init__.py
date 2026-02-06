"""
Domain Models 模块

数据库模型定义：
- base: 基础模型
- enums: 枚举定义
- user: 用户模型
- project: 项目模型
- task: 任务定义模型
- task_run: 任务执行实例模型
- runtime: 运行时环境模型
- worker: Worker 节点模型
- worker_project: Worker 项目绑定模型
- crawl: 爬取批次模型
- monitoring: 监控模型
- audit_log: 审计日志模型
- system_config: 系统配置模型
"""

# 基础模型
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
    InterpreterSource,
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
    ProjectFileVersion,
    ProjectRule,
)

# 运行时环境模型
from antcode_core.domain.models.runtime import (
    Interpreter,
    ProjectRuntimeBinding,
    Runtime,
)

# 系统配置模型
from antcode_core.domain.models.system_config import SystemConfig

# 任务模型
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun

# 用户模型
from antcode_core.domain.models.user import User, pwd_context

# Worker 节点模型
from antcode_core.domain.models.worker import (
    UserWorkerPermission,
    Worker,
    WorkerHeartbeat,
)

# Worker 安装 Key 模型
from antcode_core.domain.models.worker_install_key import WorkerInstallKey

# Worker 项目绑定模型
from antcode_core.domain.models.worker_project import (
    WorkerProject,
    WorkerProjectFile,
)

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
    "InterpreterSource",
    "WorkerStatus",
    "AuditAction",
    "BatchStatus",
    "Priority",
    # 用户模型
    "User",
    "pwd_context",
    # 项目模型
    "Project",
    "ProjectFile",
    "ProjectFileVersion",
    "ProjectRule",
    "ProjectCode",
    # 任务模型
    "Task",
    "TaskRun",
    # 运行时环境模型
    "Interpreter",
    "Runtime",
    "ProjectRuntimeBinding",
    # Worker 节点模型
    "Worker",
    "WorkerHeartbeat",
    "UserWorkerPermission",
    # Worker 安装 Key 模型
    "WorkerInstallKey",
    # Worker 项目绑定模型
    "WorkerProject",
    "WorkerProjectFile",
    # 爬取批次模型
    "CrawlBatch",
    "CrawlTaskStatus",
    # 监控模型
    "WorkerPerformanceHistory",
    "SpiderMetricsHistory",
    "WorkerEvent",
    # 审计日志模型
    "AuditLog",
    # 系统配置模型
    "SystemConfig",
]
