"""运行时后端抽象层。

每种语言（Python / Node.js / Go / Java）实现独立 backend 类，
共用 mise 层管理语言二进制版本，backend 层管依赖安装 + 命令构造。

现有 UVManager/RuntimeBuilder 仍作为 Python 的默认实现路径；
新 backend 通过 `register_backend()` 挂入 Manager 分派表。
"""

from antcode_worker.runtime.backends.base import (
    BackendBuildResult,
    RuntimeBackend,
    UnsupportedLanguageError,
    get_backend,
    register_backend,
    supported_languages,
)

__all__ = [
    "BackendBuildResult",
    "RuntimeBackend",
    "UnsupportedLanguageError",
    "get_backend",
    "register_backend",
    "supported_languages",
]
