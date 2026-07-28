import stat
from pathlib import Path

import pytest
from antcode_worker.executor.artifact_io_windows_kernel import WindowsEntryInfo
from antcode_worker.services.credential.windows_io import read_private_windows_file

from tests.unit.worker.windows_artifact_fake import FakeWindowsKernel, identity

PATH = Path(r"C:\worker\secrets\worker_credentials.json")


def _kernel(content: bytes = b"credentials") -> FakeWindowsKernel:
    kernel = FakeWindowsKernel()
    kernel.add_file(str(PATH), 7, content)
    return kernel


def test_windows_credential_read_uses_verified_handle_and_closes_it() -> None:
    kernel = _kernel()

    assert read_private_windows_file(PATH, 64, kernel=kernel) == b"credentials"

    kernel.assert_all_closed()


@pytest.mark.parametrize(
    ("info", "message"),
    [
        (WindowsEntryInfo(identity(7), False, True), "reparse point"),
        (WindowsEntryInfo(identity(7, links=2), False, False), "硬链接"),
        (WindowsEntryInfo(type(identity(7))(7, 7, stat.S_IFIFO, 1, 0, 1, 1), False, False), "普通文件"),
    ],
)
def test_windows_credential_read_rejects_unsafe_entry(info, message: str) -> None:
    kernel = _kernel()
    kernel.entries[kernel._key(str(PATH))] = info

    with pytest.raises((PermissionError, ValueError), match=message):
        read_private_windows_file(PATH, 64, kernel=kernel)

    kernel.assert_all_closed()


def test_windows_credential_read_rejects_identity_change() -> None:
    kernel = _kernel()
    original = WindowsEntryInfo(identity(7, size=11), False, False)
    changed = WindowsEntryInfo(identity(7, size=11, version=2), False, False)
    kernel.set_info_sequence(str(PATH), original, changed)

    with pytest.raises(PermissionError, match="读取过程中"):
        read_private_windows_file(PATH, 64, kernel=kernel)

    kernel.assert_all_closed()
