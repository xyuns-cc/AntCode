"""Minimal real-NTFS coverage for the Windows artifact security backend."""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_worker.executor.artifact_io_windows import (
    Kernel32Api,
    WindowsArtifactSecurityError,
    hash_regular_file,
    inspect_candidate,
    inspect_work_root,
    read_verified_regular_file,
)
from antcode_worker.executor.artifacts import ArtifactManager

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows HANDLE semantics")


def _snapshot(work_root: Path, relative_path: str, *, kernel: Kernel32Api | None = None):
    root = inspect_work_root(work_root, kernel=kernel)
    candidate = inspect_candidate(work_root / relative_path, relative_path, kernel=kernel)
    assert candidate is not None
    return root, candidate


def _process_handle_count() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return count.value


def test_real_windows_regular_file_hash_and_read(tmp_path: Path) -> None:
    content = b"real windows artifact"
    artifact = tmp_path / "nested" / "result.bin"
    artifact.parent.mkdir()
    artifact.write_bytes(content)
    root, candidate = _snapshot(tmp_path, r"nested\result.bin")

    verified = hash_regular_file(
        root.path,
        r"nested\result.bin",
        expected_root=root.identity,
        expected=candidate,
        algorithm="sha256",
        max_bytes=len(content),
    )
    read = read_verified_regular_file(
        root.path,
        r"nested\result.bin",
        expected_root=root.identity,
        expected=verified.identity,
        expected_checksum=verified.checksum,
        algorithm="sha256",
        max_bytes=len(content),
    )

    assert verified.checksum == hashlib.sha256(content).hexdigest()
    assert read == content


def test_real_windows_rejects_hardlink(tmp_path: Path) -> None:
    artifact = tmp_path / "result.bin"
    artifact.write_bytes(b"linked")
    os.link(artifact, tmp_path / "alias.bin")

    with pytest.raises(WindowsArtifactSecurityError, match="硬链接"):
        inspect_candidate(artifact, artifact.name)


def test_real_windows_rejects_junction_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "result.bin").write_bytes(b"junction")
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=True,
        capture_output=True,
    )

    with pytest.raises(WindowsArtifactSecurityError, match="reparse point"):
        inspect_candidate(junction / "result.bin", r"junction\result.bin")


def test_real_windows_rejects_ads_before_open(tmp_path: Path) -> None:
    with pytest.raises(WindowsArtifactSecurityError, match="ADS"):
        inspect_candidate(tmp_path / "result.bin:secret", "result.bin:secret")


def test_real_windows_operations_do_not_leak_handles(tmp_path: Path) -> None:
    content = b"handle lifecycle"
    artifact = tmp_path / "result.bin"
    artifact.write_bytes(content)
    kernel = Kernel32Api()
    root, candidate = _snapshot(tmp_path, artifact.name, kernel=kernel)
    before = _process_handle_count()

    for _ in range(20):
        hash_regular_file(
            root.path,
            artifact.name,
            expected_root=root.identity,
            expected=candidate,
            algorithm="sha256",
            max_bytes=len(content),
            kernel=kernel,
        )

    assert _process_handle_count() == before


@pytest.mark.asyncio
async def test_real_windows_collector_and_manager_use_platform_facade(tmp_path: Path) -> None:
    content = b"facade integration"
    (tmp_path / "result.bin").write_bytes(content)
    store = AsyncMock()
    store.upload_task_artifact.return_value = SimpleNamespace(
        uri="pgartifact://stored",
        content_hash="stored",
        size_bytes=len(content),
    )
    manager = ArtifactManager(artifact_store=store)

    result = await manager.collect_artifacts(str(tmp_path), ["result.bin"], "run-1")
    await manager.store_artifact(result.artifacts[0], "run-1")

    request = store.upload_task_artifact.await_args.args[0]
    assert request.content == content
    assert request.media_type == "application/octet-stream"
    assert request.run_id == "run-1"
