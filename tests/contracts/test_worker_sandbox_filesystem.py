"""Deployment contract for the Worker payload filesystem namespace."""

from __future__ import annotations

import sys
from pathlib import Path

from antcode_worker.executor import sandbox_mounts
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig


def _mounts(command: list[str], option: str) -> set[tuple[str, str]]:
    return {(command[index + 1], command[index + 2]) for index, value in enumerate(command[:-2]) if value == option}


def test_payload_namespace_never_mounts_worker_or_runtime_collection_roots(tmp_path: Path) -> None:
    data_root = tmp_path / "worker-data"
    work_dir = data_root / "runs" / "sources" / "run-current" / "project"
    runtime = data_root / "runtimes" / "private-current"
    other_runtime = data_root / "runtimes" / "private-other"
    shared_cache = data_root / "cache"
    for path in (work_dir, runtime, other_runtime, shared_cache):
        path.mkdir(parents=True)

    sandbox = BasicSandbox(
        SandboxConfig(
            sandbox_command=["/usr/bin/bwrap"],
            network_isolated=True,
            data_dir=str(data_root),
            runtimes_dir=str(data_root / "runtimes"),
        )
    )
    command = sandbox.wrap_command(
        [sys.executable, "main.py"],
        {
            "work_dir": str(work_dir),
            "plugin_name": "code",
            "run_id": "run-current",
            "runtime_path": str(runtime),
            "runtime_executable": sys.executable,
        },
    )
    read_only = _mounts(command, "--ro-bind")
    writable = _mounts(command, "--bind")
    destinations = {destination for _source, destination in (*read_only, *writable)}

    assert (str(runtime), str(runtime)) in read_only
    assert (str(work_dir), str(work_dir)) in writable
    assert "/" not in destinations
    assert str(data_root) not in destinations
    assert str(data_root / "runtimes") not in destinations
    assert str(other_runtime) not in destinations
    assert str(shared_cache) not in destinations
    shm_index = command.index("--tmpfs")
    assert command[shm_index : shm_index + 2] == ["--tmpfs", "/dev/shm"]
    chmod_index = command.index("--chmod")
    assert command[chmod_index : chmod_index + 3] == ["--chmod", "1777", "/dev/shm"]


def test_internal_browser_plugins_receive_all_trusted_application_packages(
    monkeypatch,
    tmp_path: Path,
) -> None:
    packages = {
        name: tmp_path / name for name in ("antcode_worker", "antcode_scrapy", "antcode_core", "antcode_contracts")
    }
    for path in packages.values():
        path.mkdir()
    monkeypatch.setattr(sandbox_mounts, "_package_path", lambda name: packages[name])

    for plugin_name in ("render", "rule", "spider"):
        assert set(sandbox_mounts._trusted_application_paths(plugin_name)) == set(packages.values())
