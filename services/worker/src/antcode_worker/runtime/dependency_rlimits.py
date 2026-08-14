"""POSIX resource limits for dependency preparation subprocesses."""

from __future__ import annotations

import os
from collections.abc import Callable

try:
    import resource
except ImportError:  # pragma: no cover - bwrap is unavailable on Windows
    resource = None  # type: ignore[assignment]

DEPENDENCY_MAX_OPEN_FILES = 2048
DEPENDENCY_MAX_FILE_MB = 256


def build_dependency_preexec(*, cpu_seconds: int, memory_mb: int) -> Callable[[], None]:
    """Build the child-side POSIX rlimit initializer."""
    if resource is None:
        raise RuntimeError("依赖准备资源限制仅支持 POSIX 平台")

    def apply_limits() -> None:
        os.setsid()
        _set_limit(resource.RLIMIT_CPU, cpu_seconds)
        _set_limit(resource.RLIMIT_AS, memory_mb * 1024 * 1024)
        _set_limit(resource.RLIMIT_NOFILE, DEPENDENCY_MAX_OPEN_FILES)
        _set_limit(resource.RLIMIT_FSIZE, DEPENDENCY_MAX_FILE_MB * 1024 * 1024)

    return apply_limits


def _set_limit(kind: int, requested: int) -> None:
    if resource is None:
        raise RuntimeError("POSIX resource module unavailable")
    _, hard = resource.getrlimit(kind)
    applied = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
    resource.setrlimit(kind, (applied, applied))


__all__ = ["build_dependency_preexec"]
