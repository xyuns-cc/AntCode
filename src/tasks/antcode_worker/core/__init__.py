"""核心层

包含系统的核心组件：
- 引擎：任务调度和执行的核心
- 调度器：任务队列管理
- 信号：事件通知系统
"""

from .engine import WorkerEngine, EngineConfig, EngineState, get_worker_engine, init_worker_engine
from .scheduler import (
    Scheduler,
    PriorityScheduler,
    BatchReceiver,
    ScheduledTask,
    PriorityTask,
    QueueStatus,
    TaskItem,
    BatchTaskRequest,
    BatchTaskResponse,
    TaskPriority,
    ProjectType,
    get_default_priority,
)
from .signals import SignalManager, Signal, signal_manager

__all__ = [
    # 引擎
    "WorkerEngine",
    "EngineConfig",
    "EngineState",
    "get_worker_engine",
    "init_worker_engine",
    # 调度器
    "Scheduler",
    "PriorityScheduler",
    "BatchReceiver",
    "ScheduledTask",
    "PriorityTask",
    "QueueStatus",
    "TaskItem",
    "BatchTaskRequest",
    "BatchTaskResponse",
    "TaskPriority",
    "ProjectType",
    "get_default_priority",
    # 信号
    "SignalManager",
    "Signal",
    "signal_manager",
]
