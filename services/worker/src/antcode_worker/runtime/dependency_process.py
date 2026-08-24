"""Resource-bounded, network-isolated dependency preparation commands."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import shutil
import signal
import stat
from dataclasses import dataclass
from pathlib import Path

import psutil

from antcode_worker.config import DATA_ROOT
from antcode_worker.domain.models import RunContext
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig
from antcode_worker.runtime.dependency_rlimits import build_dependency_preexec

DEPENDENCY_TIMEOUT_SECONDS = 600
DEPENDENCY_CPU_SECONDS = 300
DEPENDENCY_MEMORY_MB = 512
DEPENDENCY_MAX_PROCESSES = 64
DEPENDENCY_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEPENDENCY_WORKSPACE_MAX_BYTES = 512 * 1024 * 1024
_MONITOR_INTERVAL_SECONDS = 0.1
_BWRAP_NAME = "bwrap"


@dataclass(frozen=True)
class DependencyLimits:
    timeout_seconds: int
    cpu_seconds: int
    memory_mb: int
    workspace_bytes: int = DEPENDENCY_WORKSPACE_MAX_BYTES
    run_id: str | None = None

    @classmethod
    def from_context(cls, context: RunContext) -> DependencyLimits:
        timeout = max(1, min(context.timeout_seconds or DEPENDENCY_TIMEOUT_SECONDS, DEPENDENCY_TIMEOUT_SECONDS))
        cpu = context.cpu_limit_seconds or min(timeout, DEPENDENCY_CPU_SECONDS)
        memory = context.memory_limit_mb or DEPENDENCY_MEMORY_MB
        return cls(
            timeout_seconds=timeout,
            cpu_seconds=cpu,
            memory_mb=memory,
            run_id=context.run_id,
        )


@dataclass(frozen=True)
class DependencyCommandResult:
    exit_code: int
    stdout: str
    stderr: str


class DependencyPreparationError(RuntimeError):
    """Dependency preparation violated an isolation or resource contract."""


async def run_dependency_command(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    limits: DependencyLimits,
) -> DependencyCommandResult:
    root = Path(cwd).resolve(strict=True)
    _require_workspace_budget(root, limits.workspace_bytes)
    wrapped = _wrap_offline_command(command, root, run_id=limits.run_id, memory_mb=limits.memory_mb)
    process = await _start_process(wrapped, root=root, env=env, limits=limits)
    capture = asyncio.create_task(_capture_process(process))
    monitor = asyncio.create_task(_monitor_process(process, root, limits))
    try:
        result = await _wait_for_command(
            process,
            capture=capture,
            monitor=monitor,
            timeout_seconds=limits.timeout_seconds,
        )
        _require_workspace_budget(root, limits.workspace_bytes)
        return result
    except BaseException:
        await _kill_process_group(process)
        await _settle_task(capture)
        raise
    finally:
        monitor.cancel()
        await _settle_task(monitor)


def _wrap_offline_command(command: list[str], root: Path, *, run_id: str | None, memory_mb: int) -> list[str]:
    sandbox_command = _resolve_bwrap_command()
    sandbox = BasicSandbox(
        SandboxConfig(
            enabled=True,
            network_isolated=True,
            fs_isolated=True,
            sandbox_command=sandbox_command,
            data_dir=str(_workspace_data_root(root)),
        )
    )
    context = {
        "work_dir": str(root),
        "allow_network": False,
        "plugin_name": "dependency_prepare",
        "run_id": run_id,
        "payload_max_processes": DEPENDENCY_MAX_PROCESSES,
        # 依赖准备也跑在同一套 bwrap 画像里，同样有"tmpfs 页计入容器内存却不进 RSS"
        # 的盲区（_resource_violation 只看 memory_info().rss）。尺寸取本次准备自己的
        # 内存上限，与任务执行路径同一条规则。
        "tmpfs_size_mb": memory_mb,
    }
    return sandbox.wrap_command(command, context)


def _resolve_bwrap_command() -> list[str]:
    configured = os.getenv("WORKER_SANDBOX_COMMAND", _BWRAP_NAME)
    parts = shlex.split(configured)
    if len(parts) != 1 or os.path.basename(parts[0]) != _BWRAP_NAME:
        raise DependencyPreparationError("依赖准备只支持单一 bwrap 沙箱启动器")
    executable = shutil.which(parts[0])
    if executable is None:
        raise DependencyPreparationError("依赖准备需要 bwrap，但未找到可执行文件")
    return [executable]


def _workspace_data_root(root: Path) -> Path:
    for candidate in (root, *root.parents):
        if candidate.name == "sources" and candidate.parent.name == "runs":
            return candidate.parent.parent
    return DATA_ROOT


async def _start_process(
    command: list[str],
    *,
    root: Path,
    env: dict[str, str],
    limits: DependencyLimits,
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_build_preexec(limits),
    )


def _build_preexec(limits: DependencyLimits):
    try:
        return build_dependency_preexec(
            cpu_seconds=limits.cpu_seconds,
            memory_mb=limits.memory_mb,
        )
    except RuntimeError as exc:
        raise DependencyPreparationError(str(exc)) from exc


async def _capture_process(process: asyncio.subprocess.Process) -> tuple[int, bytes, bytes]:
    budget = [0]
    stdout_task = asyncio.create_task(_read_bounded(process.stdout, budget))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, budget))
    try:
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        return await process.wait(), stdout, stderr
    except BaseException:
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise


async def _read_bounded(stream: asyncio.StreamReader | None, budget: list[int]) -> bytes:
    if stream is None:
        return b""
    output = bytearray()
    while chunk := await stream.read(64 * 1024):
        budget[0] += len(chunk)
        if budget[0] > DEPENDENCY_MAX_OUTPUT_BYTES:
            raise DependencyPreparationError(f"依赖准备输出超过上限 {DEPENDENCY_MAX_OUTPUT_BYTES} 字节")
        output.extend(chunk)
    return bytes(output)


async def _wait_for_command(
    process: asyncio.subprocess.Process,
    *,
    capture: asyncio.Task,
    monitor: asyncio.Task,
    timeout_seconds: int,
) -> DependencyCommandResult:
    done, _ = await asyncio.wait(
        {capture, monitor},
        timeout=timeout_seconds,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if not done:
        raise DependencyPreparationError(f"依赖准备超过超时上限 {timeout_seconds} 秒")
    if monitor in done:
        violation = monitor.result()
        if violation:
            raise DependencyPreparationError(violation)
    exit_code, stdout, stderr = await capture
    return DependencyCommandResult(exit_code, stdout.decode(errors="replace"), stderr.decode(errors="replace"))


async def _monitor_process(
    process: asyncio.subprocess.Process,
    root: Path,
    limits: DependencyLimits,
) -> str | None:
    parent = psutil.Process(process.pid)
    while process.returncode is None:
        violation = _resource_violation(parent, limits)
        if violation:
            return violation
        size = await asyncio.to_thread(_workspace_size, root)
        if size > limits.workspace_bytes:
            return f"依赖准备工作区超过上限 {limits.workspace_bytes} 字节"
        await asyncio.sleep(_MONITOR_INTERVAL_SECONDS)
    return None


def _resource_violation(parent: psutil.Process, limits: DependencyLimits) -> str | None:
    try:
        processes = [parent, *parent.children(recursive=True)]
        memory = sum(item.memory_info().rss for item in processes if item.is_running())
        cpu = sum(sum(item.cpu_times()[:2]) for item in processes if item.is_running())
    except psutil.NoSuchProcess:
        return None
    if memory > limits.memory_mb * 1024 * 1024:
        return f"依赖准备内存超过上限 {limits.memory_mb} MiB"
    if cpu > limits.cpu_seconds:
        return f"依赖准备 CPU 时间超过上限 {limits.cpu_seconds} 秒"
    return None


def _require_workspace_budget(root: Path, maximum: int) -> None:
    size = _workspace_size(root)
    if size > maximum:
        raise DependencyPreparationError(f"依赖准备工作区已超过上限 {maximum} 字节")


def _workspace_size(root: Path) -> int:
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    total += max(entry_stat.st_size, getattr(entry_stat, "st_blocks", 0) * 512)
                    if stat.S_ISDIR(entry_stat.st_mode):
                        stack.append(Path(entry.path))
        except FileNotFoundError:
            continue
    return total


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def _settle_task(task: asyncio.Task) -> None:
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


__all__ = [
    "DependencyCommandResult",
    "DependencyLimits",
    "DependencyPreparationError",
    "run_dependency_command",
]
