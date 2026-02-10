"""
运行时管理模块

提供 Python 虚拟环境管理、缓存、锁和垃圾回收功能。

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

# 规格定义
# 构建器
from antcode_worker.runtime.builder import (
    BuildResult,
    RuntimeBuilder,
)

# 垃圾回收
from antcode_worker.runtime.gc import (
    GCPolicy,
    GCStats,
    RuntimeGC,
    RuntimeInfo,
    get_runtime_gc,
)

# 哈希计算
from antcode_worker.runtime.hash import (
    RuntimeHasher,
    compute_content_hash,
    compute_file_hash,
    compute_requirements_hash,
    compute_runtime_hash,
    get_hasher,
    verify_runtime_hash,
)

# 并发锁
from antcode_worker.runtime.locks import (
    FileLock,
    LockInfo,
    LockStats,
    RuntimeLock,
    get_file_lock,
    get_runtime_lock,
)

# 管理器
from antcode_worker.runtime.manager import (
    RuntimeManager,
    RuntimeManagerConfig,
    create_runtime_manager,
    get_runtime_manager,
    set_runtime_manager,
)
from antcode_worker.runtime.spec import (
    LockSource,
    PythonSpec,
    RuntimeSpec,
)

__all__ = [
    # 规格定义
    "PythonSpec",
    "LockSource",
    "RuntimeSpec",
    # 哈希计算
    "compute_runtime_hash",
    "compute_content_hash",
    "compute_requirements_hash",
    "compute_file_hash",
    "verify_runtime_hash",
    "RuntimeHasher",
    "get_hasher",
    # 构建器
    "RuntimeBuilder",
    "BuildResult",
    # 并发锁
    "RuntimeLock",
    "FileLock",
    "LockInfo",
    "LockStats",
    "get_runtime_lock",
    "get_file_lock",
    # 垃圾回收
    "RuntimeGC",
    "GCPolicy",
    "GCStats",
    "RuntimeInfo",
    "get_runtime_gc",
    # 管理器
    "RuntimeManager",
    "RuntimeManagerConfig",
    "create_runtime_manager",
    "get_runtime_manager",
    "set_runtime_manager",
]
