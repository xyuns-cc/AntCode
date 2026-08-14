"""Bubblewrap sandbox providers with a fail-closed filesystem view."""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from antcode_worker.config import DATA_ROOT
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor.rule_network_policy import (
    bridge_mount_args,
    rule_bridge_socket,
    wrap_rule_command,
)
from antcode_worker.executor.rule_policy import RULE_PLUGIN_ENV_VARS
from antcode_worker.executor.sandbox_config import SandboxConfig
from antcode_worker.executor.sandbox_mounts import SandboxFilesystemRequest, sandbox_filesystem_args

_PRLIMIT_EXECUTABLE = "prlimit"
_PAYLOAD_PROCESS_LIMIT_KEY = "payload_max_processes"
_PROCESS_HIJACK_ENV = frozenset({"BASH_ENV", "ENV", "PYTHONHOME"})
_PROCESS_HIJACK_PREFIXES = ("LD_", "DYLD_")
_SENSITIVE_ENV_PATTERNS = frozenset({"SECRET", "PASSWORD", "TOKEN", "API_KEY", "CREDENTIAL", "PRIVATE"})


class SandboxProvider(ABC):
    """Interface for preparing, wrapping, and cleaning a task sandbox."""

    @abstractmethod
    async def prepare(self, exec_plan: ExecPlan, work_dir: str) -> dict[str, Any]:
        """Prepare and return the per-task sandbox context."""

    @abstractmethod
    def wrap_command(self, cmd: list[str], context: dict[str, Any]) -> list[str]:
        """Wrap a payload command with the isolation provider."""

    @abstractmethod
    def filter_env(self, env: dict[str, str], context: dict[str, Any]) -> dict[str, str]:
        """Return only environment variables permitted for the payload."""

    @abstractmethod
    async def cleanup(self, context: dict[str, Any]) -> None:
        """Remove resources created while preparing the sandbox."""


class NoOpSandbox(SandboxProvider):
    """Test-only provider that performs no isolation."""

    async def prepare(self, exec_plan: ExecPlan, work_dir: str) -> dict[str, Any]:
        del exec_plan
        return {"work_dir": work_dir}

    def wrap_command(self, cmd: list[str], context: dict[str, Any]) -> list[str]:
        del context
        return cmd

    def filter_env(self, env: dict[str, str], context: dict[str, Any]) -> dict[str, str]:
        del context
        return env

    async def cleanup(self, context: dict[str, Any]) -> None:
        del context


class BasicSandbox(SandboxProvider):
    """Bubblewrap provider used for untrusted Worker payloads."""

    def __init__(self, config: SandboxConfig):
        self.config = config

    async def prepare(self, exec_plan: ExecPlan, work_dir: str) -> dict[str, Any]:
        context: dict[str, Any] = {
            "original_work_dir": work_dir,
            "temp_work_dir": None,
            "cleanup_dirs": [],
            "plugin_name": exec_plan.plugin_name,
            "run_id": exec_plan.run_id,
            "rule_egress_socket": rule_bridge_socket(exec_plan),
        }
        if self.config.fs_isolated and not self.config.sandbox_command:
            self._prepare_temporary_workspace(context, exec_plan)
        else:
            context["work_dir"] = work_dir
        return context

    def _prepare_temporary_workspace(self, context: dict[str, Any], exec_plan: ExecPlan) -> None:
        temp_base = self.config.temp_dir or str(DATA_ROOT / "temp" / "sandbox")
        os.makedirs(temp_base, exist_ok=True)
        temp_work_dir = os.path.join(temp_base, f"sandbox_{os.getpid()}_{id(exec_plan)}")
        os.makedirs(temp_work_dir, exist_ok=True)
        context["temp_work_dir"] = temp_work_dir
        context["cleanup_dirs"].append(temp_work_dir)
        context["work_dir"] = temp_work_dir

    def wrap_command(self, cmd: list[str], context: dict[str, Any]) -> list[str]:
        self._require_bwrap()
        work_dir = self._resolve_work_dir(context)
        wrapped = self._namespace_args(context)
        wrapped.extend(self._filesystem_args(cmd, context, work_dir))
        wrapped.extend(bridge_mount_args(context))
        wrapped.extend(["--chdir", str(work_dir), "--"])
        payload = wrap_rule_command(cmd, context)
        return [*wrapped, *self._limit_payload_processes(payload, context)]

    def _require_bwrap(self) -> None:
        command = self.config.sandbox_command
        if not command:
            raise RuntimeError("真实沙箱命令未配置，拒绝执行用户任务")
        if not os.path.isabs(command[0]):
            raise RuntimeError(f"沙箱启动命令必须是绝对路径,当前 {command[0]!r} 会被任务 env.PATH 劫持")
        executable = os.path.basename(command[0])
        if executable != "bwrap":
            raise RuntimeError(f"仅支持 bwrap 沙箱,拒绝执行未知启动器 {executable!r}; 其他前缀命令会绕过隔离语义")

    @staticmethod
    def _resolve_work_dir(context: dict[str, Any]) -> Path:
        try:
            return Path(context["work_dir"]).resolve(strict=True)
        except (KeyError, OSError) as exc:
            raise RuntimeError("任务工作目录不可用，拒绝启动 sandbox") from exc

    def _namespace_args(self, context: dict[str, Any]) -> list[str]:
        args = [
            *self.config.sandbox_command,  # type: ignore[misc]
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
        ]
        if self.config.network_isolated or context.get("plugin_name") == "rule":
            args.append("--unshare-net")
        return args

    def _filesystem_args(
        self,
        cmd: list[str],
        context: dict[str, Any],
        work_dir: Path,
    ) -> tuple[str, ...]:
        return sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=cmd[0],
                data_root=Path(self.config.data_dir),
                runtimes_root=Path(self.config.runtimes_dir or Path(self.config.data_dir) / "runtimes"),
                plugin_name=context.get("plugin_name"),
                run_id=self._context_string(context, "run_id"),
                runtime_dir=self._context_path(context, "runtime_path"),
                runtime_executable=self._context_path(context, "runtime_executable"),
            )
        )

    @staticmethod
    def _limit_payload_processes(cmd: list[str], context: dict[str, Any]) -> list[str]:
        limit = int(context.get(_PAYLOAD_PROCESS_LIMIT_KEY, 0))
        if limit <= 0:
            return cmd
        prlimit = shutil.which(_PRLIMIT_EXECUTABLE)
        if not prlimit:
            raise RuntimeError("启用 bwrap 进程限制需要 prlimit")
        return [prlimit, f"--nproc={limit}:{limit}", "--", *cmd]

    @staticmethod
    def _context_path(context: dict[str, Any], key: str) -> Path | None:
        value = context.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"sandbox context.{key} 必须是非空绝对路径")
        return Path(value)

    @staticmethod
    def _context_string(context: dict[str, Any], key: str) -> str | None:
        value = context.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"sandbox context.{key} 必须是非空字符串")
        return value

    def filter_env(self, env: dict[str, str], context: dict[str, Any]) -> dict[str, str]:
        task_keys = frozenset(context.get("task_env_keys", ()))
        allowed = set(self.config.allowed_env_vars)
        if context.get("plugin_name") == "rule":
            allowed.update(RULE_PLUGIN_ENV_VARS)
        else:
            allowed.update(task_keys)
        return {key: env[key] for key in allowed if key in env and self._environment_key_allowed(key, task_keys)}

    @staticmethod
    def _environment_key_allowed(key: str, task_keys: frozenset[str]) -> bool:
        if key in _PROCESS_HIJACK_ENV or key.startswith(_PROCESS_HIJACK_PREFIXES):
            return False
        sensitive = any(pattern in key.upper() for pattern in _SENSITIVE_ENV_PATTERNS)
        return not sensitive or key in task_keys

    async def cleanup(self, context: dict[str, Any]) -> None:
        if not self.config.cleanup_on_exit:
            return
        for dir_path in context.get("cleanup_dirs", []):
            try:
                if os.path.exists(dir_path):
                    shutil.rmtree(dir_path)
                    logger.debug("已清理沙箱目录: {}", dir_path)
            except Exception as exc:
                logger.warning("清理沙箱目录失败: {}, error={}", dir_path, exc)


__all__ = ["BasicSandbox", "NoOpSandbox", "SandboxProvider"]
