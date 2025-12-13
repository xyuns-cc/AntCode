"""引擎模块 - 已迁移到 core/

所有功能已迁移到:
- core/engine.py - WorkerEngine
- core/scheduler.py - Scheduler, BatchReceiver
- core/signals.py - SignalManager, Signal
- executors/ - CodeExecutor, SpiderExecutor

请使用新的导入路径:
    from antcode_worker.core import WorkerEngine, Scheduler
    from antcode_worker.executors import CodeExecutor
"""

# 重导出以保持部分兼容性
from ..core import (
    WorkerEngine,
    EngineConfig,
    EngineState,
    Scheduler,
    Signal,
    SignalManager,
    signal_manager,
)

__all__ = [
    "WorkerEngine",
    "EngineConfig", 
    "EngineState",
    "Scheduler",
    "Signal",
    "SignalManager",
    "signal_manager",
]
