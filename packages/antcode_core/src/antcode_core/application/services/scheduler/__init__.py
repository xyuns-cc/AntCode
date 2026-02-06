"""调度服务"""

from antcode_core.application.services.scheduler.memory_queue import MemoryQueueBackend
from antcode_core.application.services.scheduler.queue_backend import (
    QueuedTask,
    TaskQueueBackend,
    get_queue_backend,
    get_queue_backend_type,
    init_queue_backend,
    reset_queue_backend,
    shutdown_queue_backend,
)
from antcode_core.application.services.scheduler.retry_service import (
    RetryConfig,
    RetryService,
    RetryStrategy,
    retry_service,
)
from antcode_core.application.services.scheduler.scheduler_service import SchedulerService, scheduler_service
from antcode_core.application.services.scheduler.task_persistence import (
    CheckpointState,
    TaskCheckpoint,
    TaskPersistenceService,
    TaskRecoveryService,
    task_persistence_service,
    task_recovery_service,
)

__all__ = [
    "SchedulerService",
    "scheduler_service",
    "RetryService",
    "retry_service",
    "RetryConfig",
    "RetryStrategy",
    "TaskCheckpoint",
    "CheckpointState",
    "TaskPersistenceService",
    "TaskRecoveryService",
    "task_persistence_service",
    "task_recovery_service",
    # 队列后端
    "TaskQueueBackend",
    "QueuedTask",
    "MemoryQueueBackend",
    "get_queue_backend",
    "get_queue_backend_type",
    "reset_queue_backend",
    "init_queue_backend",
    "shutdown_queue_backend",
]
