"""
Worker 域模型

定义 Worker 执行侧所需的最小模型集合。
"""

from antcode_worker.domain.enums import (
    ExitReason,
    LogStream,
    RunStatus,
    TaskType,
)
from antcode_worker.domain.errors import (
    ExecutionError,
    RuntimeError,
    TransportError,
    WorkerError,
)
from antcode_worker.domain.models import (
    ArtifactRef,
    ExecPlan,
    ExecResult,
    LogEntry,
    RunContext,
    TaskPayload,
)

__all__ = [
    # Models
    "RunContext",
    "TaskPayload",
    "ExecPlan",
    "ExecResult",
    "LogEntry",
    "ArtifactRef",
    # Enums
    "RunStatus",
    "LogStream",
    "TaskType",
    "ExitReason",
    # Errors
    "WorkerError",
    "ExecutionError",
    "TransportError",
    "RuntimeError",
]
