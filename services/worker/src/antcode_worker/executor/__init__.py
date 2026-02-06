"""
执行器模块

提供任务执行的各种实现。

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
"""

from antcode_worker.executor.artifacts import (
    ArtifactCollector,
    ArtifactCollectorConfig,
    ArtifactManager,
    CollectionResult,
    collect_artifacts,
    compute_file_checksum,
)
from antcode_worker.executor.base import (
    BaseExecutor,
    BufferedLogSink,
    CallbackLogSink,
    ExecutorConfig,
    ExecutorStats,
    LogSink,
    NoOpLogSink,
)
from antcode_worker.executor.limits import (
    ConcurrencyLimiter,
    ProcessTerminator,
    ResourceLimits,
    ResourceMonitor,
    ResourceUsage,
    TimeoutManager,
    get_process_limits,
    set_process_limits,
    with_timeout,
)
from antcode_worker.executor.process import ProcessExecutor, ProcessInfo
from antcode_worker.executor.sandbox import (
    BasicSandbox,
    NoOpSandbox,
    SandboxConfig,
    SandboxExecutor,
    SandboxProvider,
    create_sandbox,
    create_sandbox_executor,
)

__all__ = [
    # 基础类 (base.py)
    "BaseExecutor",
    "ExecutorConfig",
    "ExecutorStats",
    "LogSink",
    "NoOpLogSink",
    "CallbackLogSink",
    "BufferedLogSink",
    # 进程执行器 (process.py)
    "ProcessExecutor",
    "ProcessInfo",
    # 资源限制 (limits.py)
    "ResourceLimits",
    "ResourceUsage",
    "ConcurrencyLimiter",
    "TimeoutManager",
    "ResourceMonitor",
    "ProcessTerminator",
    "with_timeout",
    "get_process_limits",
    "set_process_limits",
    # 沙箱 (sandbox.py)
    "SandboxConfig",
    "SandboxProvider",
    "NoOpSandbox",
    "BasicSandbox",
    "SandboxExecutor",
    "create_sandbox",
    "create_sandbox_executor",
    # 产物收集 (artifacts.py)
    "ArtifactCollectorConfig",
    "ArtifactCollector",
    "ArtifactManager",
    "CollectionResult",
    "collect_artifacts",
    "compute_file_checksum",
]
