"""Resource-bounded synchronous Git command execution."""

from __future__ import annotations

import concurrent.futures
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from antcode_core.common.log_sanitization import sanitize_log_message

_POLL_INTERVAL_SECONDS = 0.1
# git 把结论写在 stderr 末尾（前面多是 Cloning into/Receiving objects 进度噪音），
# 所以截断保留尾部。上限远小于 GIT_MAX_COMMAND_OUTPUT_BYTES：那是防内存爆的传输闸，
# 不是给人读的错误消息长度。
_STDERR_DETAIL_MAX_CHARS = 2000
_DNS_RESOLVER_WORKERS = 2
_DNS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_DNS_RESOLVER_WORKERS,
    thread_name_prefix="bounded-dns",
)
_DNS_CAPACITY = threading.BoundedSemaphore(_DNS_RESOLVER_WORKERS)
FailureProbe = Callable[[], Exception | None]


class BoundedGitCommandError(subprocess.CalledProcessError):
    """git 失败异常，``str()`` 里带上 git 自己的诊断。

    为什么需要它：``CalledProcessError.__str__`` 只有 "Command '[...]' returned
    non-zero exit status 128."，git 真正说明问题的那句话全在 ``stderr`` 属性里，
    没有任何调用方读。结果是"远端不可达"/"分支不存在"/"认证被拒"三种完全不同、
    用户处置方式也完全不同的失败，对外长得一模一样。

    为什么必须脱敏：git 的报错会把 remote URL 原样回显
    （``fatal: Authentication failed for 'https://x:ghp_xxx@host/r.git'``），
    而这条消息会同时进 HTTP 响应体和 ``git_repositories.last_scan_error`` 列。
    不在这里过滤等于把凭证写进库并展示给前端。
    """

    def __str__(self) -> str:
        summary = super().__str__()
        detail = str(self.stderr or "").strip()
        if not detail:
            return sanitize_log_message(summary)
        return sanitize_log_message(f"{summary}\n{detail[-_STDERR_DETAIL_MAX_CHARS:]}")


@dataclass(frozen=True)
class GitCommandLimits:
    timeout_seconds: float
    max_output_bytes: int
    max_repository_bytes: int


def run_bounded_git_command(
    command: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    limits: GitCommandLimits,
    quota_path: Path | None = None,
    failure_probe: FailureProbe | None = None,
) -> subprocess.CompletedProcess[str]:
    deadline = time.monotonic() + limits.timeout_seconds
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=os.name != "nt",
        )
        failure = _wait_for_git_process(
            process,
            deadline=deadline,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            quota_path=quota_path,
            limits=limits,
            failure_probe=failure_probe,
        )
        if failure is not None:
            _terminate_process(process)
            raise failure
        stdout = _read_output(stdout_file, limits.max_output_bytes)
        stderr = _read_output(stderr_file, limits.max_output_bytes)
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if process.returncode:
        raise BoundedGitCommandError(process.returncode, command, stdout, stderr)
    return result


def _wait_for_git_process(
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
    stdout_file,
    stderr_file,
    quota_path: Path | None,
    limits: GitCommandLimits,
    failure_probe: FailureProbe | None,
) -> Exception | None:
    while process.poll() is None:
        failure = _current_git_failure(
            process=process,
            deadline=deadline,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            quota_path=quota_path,
            limits=limits,
            failure_probe=failure_probe,
        )
        if failure is not None:
            return failure
        time.sleep(_POLL_INTERVAL_SECONDS)
    return _current_git_failure(
        process=process,
        deadline=deadline,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
        quota_path=quota_path,
        limits=limits,
        failure_probe=failure_probe,
    )


def _current_git_failure(
    *,
    process: subprocess.Popen[bytes],
    deadline: float,
    stdout_file,
    stderr_file,
    quota_path: Path | None,
    limits: GitCommandLimits,
    failure_probe: FailureProbe | None,
) -> Exception | None:
    external_failure = failure_probe() if failure_probe is not None else None
    if external_failure is not None:
        return external_failure
    if process.poll() is None and time.monotonic() >= deadline:
        return subprocess.TimeoutExpired(process.args, limits.timeout_seconds)
    output_size = os.fstat(stdout_file.fileno()).st_size + os.fstat(stderr_file.fileno()).st_size
    if output_size > limits.max_output_bytes:
        return ValueError(f"Git 命令输出超过上限 {limits.max_output_bytes} 字节")
    if (
        quota_path is not None
        and _directory_size(quota_path, limits.max_repository_bytes) > limits.max_repository_bytes
    ):
        return ValueError(f"Git 仓库磁盘占用超过上限 {limits.max_repository_bytes} 字节")
    return None


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def _directory_size(root: Path, stop_after: int) -> int:
    if not root.exists():
        return 0
    total = 0
    for current, _, filenames in os.walk(root):
        for filename in filenames:
            path = Path(current) / filename
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
            if total > stop_after:
                return total
    return total


def _read_output(file_obj, max_bytes: int) -> str:
    file_obj.seek(0)
    content = file_obj.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError(f"Git 命令输出超过上限 {max_bytes} 字节")
    return content.decode("utf-8", errors="replace")


def resolve_with_timeout(resolver: Callable[[str], object], url: str, timeout_seconds: float):
    deadline = time.monotonic() + timeout_seconds
    if not _DNS_CAPACITY.acquire(timeout=timeout_seconds):
        raise TimeoutError(f"DNS 解析等待执行池超过上限 {timeout_seconds:g} 秒")
    future = _DNS_EXECUTOR.submit(resolver, url)
    future.add_done_callback(lambda _: _DNS_CAPACITY.release())
    try:
        remaining_seconds = max(0.0, deadline - time.monotonic())
        result = future.result(timeout=remaining_seconds)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"DNS 解析超过上限 {timeout_seconds:g} 秒") from exc
    return result


def validate_git_ref(ref: str) -> None:
    if not ref or ref.startswith("-") or any(char in ref for char in "*?[\n\r\x00"):
        raise ValueError("Git 引用不合法")


def validate_repository_metadata(
    repo_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    *,
    max_objects: int,
    max_refs: int,
) -> None:
    result = runner(["git", "count-objects", "-v"], cwd=repo_dir)
    counts = _parse_count_objects(result.stdout)
    object_count = counts.get("count", 0) + counts.get("in-pack", 0)
    if object_count > max_objects:
        raise ValueError(f"Git 对象数超过上限 {max_objects}")
    refs = runner(["git", "for-each-ref", "--format=%(refname)"], cwd=repo_dir).stdout.splitlines()
    if len(refs) > max_refs:
        raise ValueError(f"Git 本地 ref 数超过上限 {max_refs}")


def _parse_count_objects(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in output.splitlines():
        key, separator, value = line.partition(": ")
        if separator and value.isdigit():
            counts[key] = int(value)
    return counts


__all__ = [
    "BoundedGitCommandError",
    "GitCommandLimits",
    "resolve_with_timeout",
    "run_bounded_git_command",
    "validate_git_ref",
    "validate_repository_metadata",
]
