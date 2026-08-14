"""Security regression tests for the per-task bubblewrap filesystem view."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from antcode_worker.executor.sandbox_mounts import SandboxFilesystemRequest, sandbox_filesystem_args


@pytest.fixture(autouse=True)
def _trust_current_uv_python(monkeypatch: pytest.MonkeyPatch) -> None:
    install_root = Path(sys.executable).resolve().parent.parent
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(install_root.parent))


def _mount_triplets(args: tuple[str, ...] | list[str], option: str) -> set[tuple[str, str]]:
    return {(args[index + 1], args[index + 2]) for index, value in enumerate(args[:-2]) if value == option}


def test_mount_view_contains_only_current_runtime_and_workspace(tmp_path: Path) -> None:
    work_dir = tmp_path / "runs" / "sources" / "run-current" / "project"
    current_runtime = tmp_path / "runtimes" / "current"
    sibling_runtime = tmp_path / "runtimes" / "other-tenant"
    shared_cache = tmp_path / "lang-cache"
    for path in (work_dir, current_runtime, sibling_runtime, shared_cache):
        path.mkdir(parents=True)

    args = sandbox_filesystem_args(
        SandboxFilesystemRequest(
            work_dir=work_dir,
            payload_executable=sys.executable,
            data_root=tmp_path,
            runtimes_root=tmp_path / "runtimes",
            run_id="run-current",
            runtime_dir=current_runtime,
        )
    )
    read_only = _mount_triplets(args, "--ro-bind")
    writable = _mount_triplets(args, "--bind")

    assert (str(current_runtime), str(current_runtime)) in read_only
    assert (str(work_dir), str(work_dir)) in writable
    assert all(str(sibling_runtime) not in mount for mount in (*read_only, *writable))
    assert all(str(shared_cache) not in mount for mount in (*read_only, *writable))
    assert ("/", "/") not in read_only
    assert _option_destinations(args, "--tmpfs") == {"/dev/shm", "/tmp"}


def _option_destinations(args: tuple[str, ...] | list[str], option: str) -> set[str]:
    return {args[index + 1] for index, value in enumerate(args[:-1]) if value == option}


@pytest.mark.parametrize("runtime", [Path("/"), Path("/tmp")])
def test_broad_runtime_mount_is_rejected(tmp_path: Path, runtime: Path) -> None:
    work_dir = tmp_path / "runs" / "sources" / "run-current" / "project"
    (tmp_path / "runtimes").mkdir()
    work_dir.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="范围过宽"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=sys.executable,
                data_root=tmp_path,
                runtimes_root=tmp_path / "runtimes",
                run_id="run-current",
                runtime_dir=runtime,
            )
        )


def test_missing_workspace_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="任务工作目录不可用"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=tmp_path / "missing",
                payload_executable=sys.executable,
                data_root=tmp_path,
                runtimes_root=tmp_path,
            )
        )


def test_runtime_collection_root_is_rejected(tmp_path: Path) -> None:
    work_dir = tmp_path / "runs" / "sources" / "run-current" / "project"
    runtimes = tmp_path / "runtimes"
    work_dir.mkdir(parents=True)
    runtimes.mkdir()

    with pytest.raises(RuntimeError, match="范围过宽"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=sys.executable,
                data_root=tmp_path,
                runtimes_root=runtimes,
                run_id="run-current",
                runtime_dir=runtimes,
            )
        )


def test_runtime_must_be_direct_child_of_configured_runtime_root(tmp_path: Path) -> None:
    work_dir = tmp_path / "runs" / "sources" / "run-current" / "project"
    runtimes = tmp_path / "runtimes"
    foreign_runtime = tmp_path / "other-storage" / "private-other"
    for path in (work_dir, runtimes, foreign_runtime):
        path.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="范围过宽"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=sys.executable,
                data_root=tmp_path,
                runtimes_root=runtimes,
                run_id="run-current",
                runtime_dir=foreign_runtime,
            )
        )


@pytest.mark.parametrize(
    "work_dir_builder",
    [
        lambda root: root / "runtimes" / "private-current",
        lambda root: root / "runs",
        lambda root: root / "runs" / "sources",
        lambda root: root / "runs" / "sources" / "run-current",
    ],
)
def test_worker_data_workspace_roots_are_rejected(tmp_path: Path, work_dir_builder) -> None:
    runtimes = tmp_path / "runtimes"
    runtimes.mkdir()
    work_dir = work_dir_builder(tmp_path)
    work_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="工作目录"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=sys.executable,
                data_root=tmp_path,
                runtimes_root=runtimes,
                run_id="run-current",
            )
        )


def test_workspace_outside_worker_data_is_rejected(tmp_path: Path) -> None:
    data_root = tmp_path / "worker-data"
    foreign_workspace = tmp_path / "foreign" / "project"
    (data_root / "runtimes").mkdir(parents=True)
    foreign_workspace.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="当前任务工作区"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=foreign_workspace,
                payload_executable=sys.executable,
                data_root=data_root,
                runtimes_root=data_root / "runtimes",
            )
        )


def test_workspace_must_match_current_run_id(tmp_path: Path) -> None:
    work_dir = tmp_path / "runs" / "sources" / "other-run" / "project"
    (tmp_path / "runtimes").mkdir()
    work_dir.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="不属于当前 run"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=sys.executable,
                data_root=tmp_path,
                runtimes_root=tmp_path / "runtimes",
                run_id="run-current",
            )
        )


def test_workspace_less_rule_uses_only_its_private_run_directory(tmp_path: Path) -> None:
    data_root = tmp_path / "worker-data"
    run_id = "run-current"
    work_dir = Path(tempfile.gettempdir()) / "antcode-rule" / run_id
    (data_root / "runtimes").mkdir(parents=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    args = sandbox_filesystem_args(
        SandboxFilesystemRequest(
            work_dir=work_dir,
            payload_executable=sys.executable,
            data_root=data_root,
            runtimes_root=data_root / "runtimes",
            plugin_name="rule",
            run_id=run_id,
        )
    )

    assert (str(work_dir.resolve()), str(work_dir.resolve())) in _mount_triplets(args, "--bind")


def test_workspace_executable_symlink_cannot_escape_current_run(tmp_path: Path) -> None:
    (tmp_path / "runtimes").mkdir()
    work_dir = tmp_path / "runs" / "sources" / "run-current" / "project"
    sibling_binary = tmp_path / "runs" / "sources" / "other-tenant" / "project" / "bin" / "tool"
    work_dir.mkdir(parents=True)
    sibling_binary.parent.mkdir(parents=True)
    sibling_binary.write_bytes(b"binary")
    task_binary = work_dir / "tool"
    task_binary.symlink_to(sibling_binary)

    with pytest.raises(RuntimeError, match="符号链接越出"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=str(task_binary),
                data_root=tmp_path,
                runtimes_root=tmp_path / "runtimes",
                run_id="run-current",
            )
        )
