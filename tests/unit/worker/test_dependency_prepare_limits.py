import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.domain.models import RunContext
from antcode_worker.runtime import dependency_process
from antcode_worker.runtime.dependency_process import (
    DependencyLimits,
    DependencyPreparationError,
    run_dependency_command,
)

_LONG_TASK_TIMEOUT_SECONDS = 1200
_EXPECTED_PREP_TIMEOUT_SECONDS = 600
_TASK_MEMORY_MB = 384
_TASK_CPU_SECONDS = 75
_WORKSPACE_LIMIT_BYTES = 1024
_GROWTH_BYTES = 4096


class _PendingProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self.returncode = None
        self.stdout = None
        self.stderr = None
        self.finished = asyncio.Event()

    async def wait(self) -> int:
        await self.finished.wait()
        return int(self.returncode or 0)


def test_dependency_limits_inherit_task_limits() -> None:
    context = RunContext(
        "run-1",
        "task-1",
        "project-1",
        timeout_seconds=_LONG_TASK_TIMEOUT_SECONDS,
        memory_limit_mb=_TASK_MEMORY_MB,
        cpu_limit_seconds=_TASK_CPU_SECONDS,
    )

    limits = DependencyLimits.from_context(context)

    assert limits.timeout_seconds == _EXPECTED_PREP_TIMEOUT_SECONDS
    assert limits.memory_mb == _TASK_MEMORY_MB
    assert limits.cpu_seconds == _TASK_CPU_SECONDS
    assert limits.run_id == "run-1"


def test_dependency_command_is_wrapped_in_offline_bwrap(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dependency_process, "_resolve_bwrap_command", lambda: ["/usr/bin/bwrap"])
    monkeypatch.setattr(
        "antcode_worker.executor.sandbox_provider.shutil.which",
        lambda name: sys.executable if name == "npm" else "/usr/bin/prlimit",
    )

    work_dir = tmp_path / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True)
    (tmp_path / "runtimes").mkdir()
    wrapped = dependency_process._wrap_offline_command(
        ["npm", "ci", "--offline"],
        work_dir,
        run_id="run-1",
    )

    assert wrapped[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in wrapped
    bind_index = wrapped.index("--bind")
    assert wrapped[bind_index : bind_index + 3] == ["--bind", str(work_dir), str(work_dir)]
    assert wrapped[-3:] == ["npm", "ci", "--offline"]


def test_dependency_command_rejects_missing_bwrap(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_SANDBOX_COMMAND", "bwrap")
    monkeypatch.setattr(dependency_process.shutil, "which", lambda _name: None)

    with pytest.raises(DependencyPreparationError, match="未找到"):
        dependency_process._resolve_bwrap_command()


@pytest.mark.asyncio
async def test_dependency_termination_targets_process_group(monkeypatch) -> None:
    process = AsyncMock()
    process.pid = 4321
    process.returncode = None
    killpg = MagicMock()
    monkeypatch.setattr(dependency_process.os, "killpg", killpg)

    await dependency_process._kill_process_group(process)

    killpg.assert_called_once_with(process.pid, dependency_process.signal.SIGKILL)
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", [None, "依赖准备工作区超过上限"])
async def test_timeout_and_quota_violation_kill_preparation_process(monkeypatch, tmp_path: Path, violation) -> None:
    process = _PendingProcess()

    async def fake_start(*_args, **_kwargs):
        return process

    async def fake_monitor(*_args, **_kwargs):
        if violation is not None:
            return violation
        await process.finished.wait()
        return None

    async def fake_kill(_process):
        process.returncode = -9
        process.finished.set()

    monkeypatch.setattr(
        dependency_process,
        "_wrap_offline_command",
        lambda command, _root, **_kwargs: command,
    )
    monkeypatch.setattr(dependency_process, "_start_process", fake_start)
    monkeypatch.setattr(dependency_process, "_monitor_process", fake_monitor)
    kill = AsyncMock(side_effect=fake_kill)
    monkeypatch.setattr(dependency_process, "_kill_process_group", kill)
    timeout = 1 if violation is not None else 0
    limits = DependencyLimits(timeout_seconds=timeout, cpu_seconds=3, memory_mb=256)

    with pytest.raises(DependencyPreparationError):
        await run_dependency_command(["npm"], cwd=str(tmp_path), env={}, limits=limits)

    kill.assert_awaited_once_with(process)


@pytest.mark.asyncio
async def test_dependency_command_rejects_workspace_growth(monkeypatch, tmp_path: Path) -> None:
    async def wait_without_monitor(process, _root, _limits):
        await process.wait()
        return None

    monkeypatch.setattr(
        dependency_process,
        "_wrap_offline_command",
        lambda command, _root, **_kwargs: command,
    )
    monkeypatch.setattr(dependency_process, "_build_preexec", lambda _limits: None)
    monkeypatch.setattr(dependency_process, "_monitor_process", wait_without_monitor)
    limits = DependencyLimits(
        timeout_seconds=5,
        cpu_seconds=3,
        memory_mb=256,
        workspace_bytes=_WORKSPACE_LIMIT_BYTES,
    )
    command = [sys.executable, "-c", f"open('growth.bin', 'wb').write(b'x' * {_GROWTH_BYTES})"]

    with pytest.raises(DependencyPreparationError, match="工作区已超过上限"):
        await run_dependency_command(command, cwd=str(tmp_path), env=os.environ.copy(), limits=limits)


def test_workspace_size_tolerates_entry_removed_during_scan(monkeypatch, tmp_path: Path) -> None:
    stable = tmp_path / "stable.bin"
    stable.write_bytes(b"1234")

    class VanishedEntry:
        def stat(self, *, follow_symlinks: bool):
            assert follow_symlinks is False
            raise FileNotFoundError

    class StableEntry:
        path = str(stable)

        def stat(self, *, follow_symlinks: bool):
            assert follow_symlinks is False
            return os.stat(stable, follow_symlinks=False)

    class Scan:
        def __enter__(self):
            return iter((VanishedEntry(), StableEntry()))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(dependency_process.os, "scandir", lambda _path: Scan())

    assert dependency_process._workspace_size(tmp_path) >= stable.stat().st_size


def test_stale_workspace_cleanup_does_not_remove_recent_run(tmp_path: Path) -> None:
    from antcode_worker.projects.fetcher import ProjectWorkspace

    stale = tmp_path / "stale-run"
    recent = tmp_path / "recent-run"
    stale.mkdir()
    recent.mkdir()
    old = time.time() - (25 * 60 * 60)
    os.utime(stale, (old, old))

    ProjectWorkspace(str(tmp_path))

    assert not stale.exists()
    assert recent.exists()


def test_stale_workspace_cleanup_unlinks_symlink_without_following_target(tmp_path: Path) -> None:
    from antcode_worker.projects.fetcher import ProjectWorkspace

    target = tmp_path.parent / "workspace-gc-target"
    target.mkdir(exist_ok=True)
    sentinel = target / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    stale_link = tmp_path / "stale-link"
    stale_link.symlink_to(target, target_is_directory=True)
    old = time.time() - (25 * 60 * 60)
    os.utime(stale_link, (old, old), follow_symlinks=False)

    ProjectWorkspace(str(tmp_path))

    assert not stale_link.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"
