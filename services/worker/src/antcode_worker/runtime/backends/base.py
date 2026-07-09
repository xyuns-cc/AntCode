"""RuntimeBackend 抽象基类。

每个 backend 负责一种语言的：
1. 依赖安装（Python: uv pip、Node: npm ci、Go: go build、Java: mvn dependency）
2. runtime_hash 计算所需的额外确定性输入
3. 运行时 handle（可执行文件路径 or 编译产物路径）
4. 命令构造（把 entry_point + args 拼成 subprocess argv）

mise 层已通过 command_runner 自动注入 MISE_* 环境变量，backend 通过
`mise exec <lang>@<ver> -- <cmd>` 获取指定版本的语言二进制。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class UnsupportedLanguageError(RuntimeError):
    """请求了未注册 backend 的语言。"""


@dataclass
class BackendBuildResult:
    """backend 构建输出。"""

    success: bool
    # 运行时产物根目录（Python: venv 目录；Node: 项目目录 with node_modules；Go: binary 所在目录）
    runtime_dir: str
    # 用于执行 entry_point 的可执行文件（Python: venv/bin/python；Node: node；Go: binary；Java: java）
    executable: str
    # 语言二进制版本（如 "3.12.1" / "20.11.0"）
    language_version: str | None = None
    error_message: str | None = None
    build_time_ms: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "runtime_dir": self.runtime_dir,
            "executable": self.executable,
            "language_version": self.language_version,
            "error_message": self.error_message,
            "build_time_ms": self.build_time_ms,
            "cached": self.cached,
        }


class RuntimeBackend(ABC):
    """运行时后端抽象基类。

    子类必须实现的方法：
    - build(): 装依赖，产出 BackendBuildResult
    - remove(): 删除构建产物
    - exists(): 幂等检查
    - list_runtimes(): 枚举本 backend 下所有产物

    Executor 通过 build_argv() 拿到最终 subprocess argv。
    """

    #: 语言标识（'python' / 'node' / 'go' / 'java'）
    language: str = ""

    @abstractmethod
    async def build(self, spec: Any, force_rebuild: bool = False) -> BackendBuildResult:
        """构建/复用运行时。"""

    @abstractmethod
    async def remove(self, runtime_hash: str) -> bool:
        """删除运行时。"""

    @abstractmethod
    def exists(self, runtime_hash: str) -> bool:
        """幂等检查。"""

    @abstractmethod
    async def list_runtimes(self) -> list[dict[str, Any]]:
        """列出本 backend 已构建的 runtime。"""

    @abstractmethod
    def build_argv(
        self,
        executable: str,
        entry_point: str,
        args: list[str],
    ) -> list[str]:
        """拼装 subprocess argv。

        - Python: [python, entry.py, *args]
        - Node:   [node, entry.js, *args]
        - Go:     [binary, *args]（entry_point 已编译为 binary，忽略 entry_point 参数）
        - Java:   [java, -jar, entry.jar, *args] 或 [java, -cp, classpath, MainClass, *args]
        """


# 语言 → backend 实例的注册表
_backends: dict[str, RuntimeBackend] = {}


def register_backend(backend: RuntimeBackend) -> None:
    """注册 backend。同名覆盖。"""
    if not backend.language:
        raise ValueError(f"backend {backend!r} 未设置 language 标识")
    _backends[backend.language] = backend


def get_backend(language: str | None) -> RuntimeBackend:
    """按语言取 backend；未指定语言默认 python 兼容旧项目。"""
    key = (language or "python").strip().lower()
    backend = _backends.get(key)
    if backend is None:
        raise UnsupportedLanguageError(
            f"未注册 language={key!r} 的 backend；"
            f"已注册: {sorted(_backends.keys())}"
        )
    return backend


def supported_languages() -> list[str]:
    """当前进程已注册的语言列表。"""
    return sorted(_backends.keys())
