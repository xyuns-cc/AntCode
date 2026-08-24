"""Executable installation root contracts for the Worker filesystem sandbox."""

from __future__ import annotations

from pathlib import Path

import pytest
from antcode_worker.executor.sandbox_mounts import SandboxFilesystemRequest, sandbox_filesystem_args


def _read_only_mounts(args: tuple[str, ...]) -> set[tuple[str, str]]:
    return {(args[index + 1], args[index + 2]) for index, value in enumerate(args[:-2]) if value == "--ro-bind"}


def test_runtime_interpreter_target_is_mounted_without_runtime_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "worker-data"
    work_dir = data_root / "runs" / "sources" / "run-current" / "project"
    runtime_dir = data_root / "runtimes" / "current"
    sibling_runtime = data_root / "runtimes" / "sibling"
    interpreter_collection = tmp_path / "python-installs"
    interpreter_root = interpreter_collection / "3.12.11"
    interpreter = interpreter_root / "bin" / "python"
    for path in (work_dir, runtime_dir / "bin", sibling_runtime, interpreter.parent):
        path.mkdir(parents=True)
    interpreter.write_bytes(b"binary")
    interpreter.chmod(0o755)
    runtime_python = runtime_dir / "bin" / "python"
    runtime_python.symlink_to(interpreter)
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(interpreter_collection))

    args = sandbox_filesystem_args(
        SandboxFilesystemRequest(
            work_dir=work_dir,
            payload_executable=str(runtime_python),
            data_root=data_root,
            runtimes_root=data_root / "runtimes",
            run_id="run-current",
            runtime_dir=runtime_dir,
            runtime_executable=runtime_python,
            tmpfs_size_mb=512,
        )
    )
    read_only = _read_only_mounts(args)

    assert (str(runtime_dir), str(runtime_dir)) in read_only
    assert (str(interpreter_root), str(interpreter_root)) in read_only
    assert all(str(runtime_dir.parent) != destination for _source, destination in read_only)
    assert all(str(sibling_runtime) not in mount for mount in read_only)


def test_external_executable_install_root_is_rejected(tmp_path: Path) -> None:
    data_root = tmp_path / "worker-data"
    work_dir = data_root / "runs" / "sources" / "run-current" / "project"
    external_root = tmp_path / "tenant-install"
    executable = external_root / "bin" / "python"
    work_dir.mkdir(parents=True)
    (data_root / "runtimes").mkdir()
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")

    with pytest.raises(RuntimeError, match="未列入 Worker 可信范围"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=str(executable),
                data_root=data_root,
                runtimes_root=data_root / "runtimes",
                run_id="run-current",
                tmpfs_size_mb=512,
            )
        )


def test_mise_language_version_root_is_mounted_without_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "worker-data"
    work_dir = data_root / "runs" / "sources" / "run-current" / "project"
    mise_data = tmp_path / "mise"
    version_root = mise_data / "installs" / "node" / "22.14.0"
    executable = version_root / "bin" / "node"
    work_dir.mkdir(parents=True)
    (data_root / "runtimes").mkdir()
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")
    monkeypatch.setenv("MISE_DATA_DIR", str(mise_data))

    args = sandbox_filesystem_args(
        SandboxFilesystemRequest(
            work_dir=work_dir,
            payload_executable=str(executable),
            data_root=data_root,
            runtimes_root=data_root / "runtimes",
            run_id="run-current",
            tmpfs_size_mb=512,
        )
    )
    read_only = _read_only_mounts(args)

    assert (str(version_root), str(version_root)) in read_only
    assert all(destination != str(mise_data / "installs") for _source, destination in read_only)


def test_mise_unknown_language_collection_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "worker-data"
    work_dir = data_root / "runs" / "sources" / "run-current" / "project"
    mise_data = tmp_path / "mise"
    executable = mise_data / "installs" / "untrusted-tool" / "1.0" / "bin" / "tool"
    work_dir.mkdir(parents=True)
    (data_root / "runtimes").mkdir()
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")
    monkeypatch.setenv("MISE_DATA_DIR", str(mise_data))

    with pytest.raises(RuntimeError, match="未列入 Worker 可信范围"):
        sandbox_filesystem_args(
            SandboxFilesystemRequest(
                work_dir=work_dir,
                payload_executable=str(executable),
                data_root=data_root,
                runtimes_root=data_root / "runtimes",
                run_id="run-current",
                tmpfs_size_mb=512,
            )
        )


def test_uv_minor_version_alias_hop_is_mounted_alongside_the_resolved_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """宿主 uv 装的解释器是两跳，只挂完全解析后的根会让 venv 软链在沙箱内悬空。

    venv/bin/python -> <collection>/cpython-3.12-<plat>/bin/python3.12（次版本别名
    目录，本身是软链）-> <collection>/cpython-3.12.13-<plat>/。别名目录没挂进
    namespace 时，任务以 `prlimit: failed to execute ...: No such file or directory`
    （退出码 127）100% 失败——物理机部署路径真机实测。
    """
    data_root = tmp_path / "worker-data"
    work_dir = data_root / "runs" / "sources" / "run-current" / "project"
    runtime_dir = data_root / "runtimes" / "current"
    collection = tmp_path / "python-installs"
    versioned_root = collection / "cpython-3.12.13-linux-x86_64-gnu"
    alias_root = collection / "cpython-3.12-linux-x86_64-gnu"
    interpreter = versioned_root / "bin" / "python3.12"
    for path in (work_dir, runtime_dir / "bin", interpreter.parent):
        path.mkdir(parents=True)
    interpreter.write_bytes(b"binary")
    interpreter.chmod(0o755)
    alias_root.symlink_to(versioned_root)
    runtime_python = runtime_dir / "bin" / "python"
    runtime_python.symlink_to(alias_root / "bin" / "python3.12")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(collection))

    args = sandbox_filesystem_args(
        SandboxFilesystemRequest(
            work_dir=work_dir,
            payload_executable=str(runtime_python),
            data_root=data_root,
            runtimes_root=data_root / "runtimes",
            run_id="run-current",
            runtime_dir=runtime_dir,
            runtime_executable=runtime_python,
            tmpfs_size_mb=512,
        )
    )
    destinations = {destination for _source, destination in _read_only_mounts(args)}

    # 别名目录必须以别名路径出现在 namespace 里（源是解析后的真实目录），
    # venv 的软链才不会悬空；解析后的根同样要挂，解释器自身的 sys.prefix 才成立。
    assert str(alias_root) in destinations
    assert str(versioned_root) in destinations
