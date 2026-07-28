"""Platform-independent tests for Windows HANDLE-based artifact access."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest
from antcode_worker.executor.artifact_io_windows import (
    WindowsArtifactSecurityError,
    hash_regular_file,
    inspect_candidate,
    inspect_work_root,
    read_verified_regular_file,
)
from antcode_worker.executor.artifact_io_windows_kernel import WindowsEntryInfo

from tests.unit.worker.windows_artifact_fake import identity, populated_kernel

WORK_ROOT = Path(r"C:\work")
RELATIVE_FILE = r"nested\result.bin"
FILE_PATH = Path(r"C:\work\nested\result.bin")


def _snapshots(kernel):
    root = inspect_work_root(WORK_ROOT, kernel=kernel)
    candidate = inspect_candidate(FILE_PATH, RELATIVE_FILE, kernel=kernel)
    assert candidate is not None
    return root, candidate


def test_hash_and_read_nested_regular_file_close_every_handle() -> None:
    content = b"verified artifact"
    kernel = populated_kernel(content)
    root, candidate = _snapshots(kernel)

    verified = hash_regular_file(
        root.path,
        RELATIVE_FILE,
        expected_root=root.identity,
        expected=candidate,
        algorithm="sha256",
        max_bytes=len(content),
        kernel=kernel,
    )
    read = read_verified_regular_file(
        root.path,
        RELATIVE_FILE,
        expected_root=root.identity,
        expected=verified.identity,
        expected_checksum=verified.checksum,
        algorithm="sha256",
        max_bytes=len(content),
        kernel=kernel,
    )

    assert verified.checksum == hashlib.sha256(content).hexdigest()
    assert read == content
    assert r"C:\work\nested" in kernel.opened_paths
    kernel.assert_all_closed()


@pytest.mark.parametrize(
    ("path", "relative_path"),
    [
        (Path(r"\\server\share\work\result.bin"), "result.bin"),
        (Path(r"\\?\C:\work\result.bin"), "result.bin"),
        (Path(r"C:\work\result.bin:secret"), "result.bin:secret"),
        (Path(r"C:\work\NUL.txt"), "NUL.txt"),
        (Path("C:\\work\\bad.\\result.bin"), "bad.\\result.bin"),
        (Path("C:\\work\\bad\x00name"), "bad\x00name"),
    ],
)
def test_unsafe_names_are_rejected_before_open(path: Path, relative_path: str) -> None:
    kernel = populated_kernel()

    with pytest.raises(WindowsArtifactSecurityError):
        inspect_candidate(path, relative_path, kernel=kernel)

    assert not kernel.opened_paths


@pytest.mark.parametrize("target", [r"C:\work\nested", r"C:\work\nested\result.bin"])
def test_reparse_parent_or_leaf_is_rejected_and_closed(target: str) -> None:
    kernel = populated_kernel()
    entry = kernel.entries[kernel._key(target)]
    kernel.entries[kernel._key(target)] = WindowsEntryInfo(entry.identity, entry.is_directory, True)

    with pytest.raises(WindowsArtifactSecurityError, match="reparse point"):
        inspect_candidate(FILE_PATH, RELATIVE_FILE, kernel=kernel)

    kernel.assert_all_closed()


def test_hardlink_is_rejected_and_closed() -> None:
    kernel = populated_kernel()
    linked = identity(4, links=2, size=len(b"artifact-data"))
    kernel.entries[kernel._key(str(FILE_PATH))] = WindowsEntryInfo(linked, False, False)

    with pytest.raises(WindowsArtifactSecurityError, match="硬链接"):
        inspect_candidate(FILE_PATH, RELATIVE_FILE, kernel=kernel)

    kernel.assert_all_closed()


@pytest.mark.parametrize("final_path", [r"C:\outside\result.bin", r"D:\work\result.bin"])
def test_final_handle_path_outside_root_is_rejected(final_path: str) -> None:
    kernel = populated_kernel()
    kernel.set_final_path(str(FILE_PATH), final_path)

    with pytest.raises(WindowsArtifactSecurityError, match="越过工作目录"):
        inspect_candidate(FILE_PATH, RELATIVE_FILE, kernel=kernel)

    kernel.assert_all_closed()


def test_root_identity_mismatch_is_rejected_and_closed() -> None:
    kernel = populated_kernel()
    _, candidate = _snapshots(kernel)

    with pytest.raises(WindowsArtifactSecurityError, match="发生变化"):
        hash_regular_file(
            WORK_ROOT,
            RELATIVE_FILE,
            expected_root=identity(999, directory=True),
            expected=candidate,
            algorithm="sha256",
            max_bytes=candidate.size,
            kernel=kernel,
        )

    kernel.assert_all_closed()


def test_file_identity_mismatch_is_rejected_and_closed() -> None:
    kernel = populated_kernel()
    root, _ = _snapshots(kernel)

    with pytest.raises(WindowsArtifactSecurityError, match="发生变化"):
        hash_regular_file(
            WORK_ROOT,
            RELATIVE_FILE,
            expected_root=root.identity,
            expected=identity(999),
            algorithm="sha256",
            max_bytes=100,
            kernel=kernel,
        )

    kernel.assert_all_closed()


def test_identity_change_during_read_is_rejected_and_closed() -> None:
    kernel = populated_kernel()
    root, candidate = _snapshots(kernel)
    changed = identity(4, size=candidate.size, version=2)
    normal = WindowsEntryInfo(candidate, False, False)
    kernel.set_info_sequence(str(FILE_PATH), normal, WindowsEntryInfo(changed, False, False))

    with pytest.raises(WindowsArtifactSecurityError, match="读取过程中"):
        hash_regular_file(
            WORK_ROOT,
            RELATIVE_FILE,
            expected_root=root.identity,
            expected=candidate,
            algorithm="sha256",
            max_bytes=candidate.size,
            kernel=kernel,
        )

    kernel.assert_all_closed()


@pytest.mark.parametrize(
    ("max_bytes", "checksum", "message"),
    [(1, None, "大小超过"), (100, "0" * 64, "内容在收集后发生变化")],
)
def test_read_rejects_size_or_checksum_mismatch(max_bytes: int, checksum: str | None, message: str) -> None:
    kernel = populated_kernel()
    root, candidate = _snapshots(kernel)

    with pytest.raises(WindowsArtifactSecurityError, match=message):
        read_verified_regular_file(
            WORK_ROOT,
            RELATIVE_FILE,
            expected_root=root.identity,
            expected=candidate,
            expected_checksum=checksum,
            algorithm="sha256",
            max_bytes=max_bytes,
            kernel=kernel,
        )

    kernel.assert_all_closed()


def test_non_regular_leaf_is_rejected() -> None:
    kernel = populated_kernel()
    special = identity(4)
    special = type(special)(7, 4, stat.S_IFIFO, 1, 0, 1, 1)
    kernel.entries[kernel._key(str(FILE_PATH))] = WindowsEntryInfo(special, False, False)

    with pytest.raises(WindowsArtifactSecurityError, match="常规文件"):
        inspect_candidate(FILE_PATH, RELATIVE_FILE, kernel=kernel)

    kernel.assert_all_closed()
