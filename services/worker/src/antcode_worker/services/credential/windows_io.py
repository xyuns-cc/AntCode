"""Secure Windows HANDLE reads for Worker credential files."""

from __future__ import annotations

import ntpath
import stat
from pathlib import Path

from antcode_worker.executor.artifact_io_windows_kernel import Kernel32Api, WindowsKernel

_READ_CHUNK_BYTES = 8192


def read_private_windows_file(
    path: Path,
    max_bytes: int,
    *,
    kernel: WindowsKernel | None = None,
) -> bytes:
    api = kernel or Kernel32Api()
    handle = api.open_entry(str(path))
    try:
        opened = api.entry_info(handle)
        _validate_entry(opened, path)
        _validate_final_path(api.final_path(handle), path)
        content = _read_bounded(api, handle, max_bytes)
        if api.entry_info(handle).identity != opened.identity:
            raise PermissionError("Worker 凭证文件在读取过程中发生变化")
        return content
    finally:
        api.close(handle)


def _validate_entry(info, path: Path) -> None:
    if info.is_reparse_point:
        raise ValueError(f"Worker 凭证文件不得是 Windows reparse point: {path}")
    identity = info.identity
    if info.is_directory or not stat.S_ISREG(identity.mode):
        raise PermissionError("Worker 凭证文件必须是普通文件")
    if identity.link_count != 1:
        raise PermissionError("Worker 凭证文件不允许硬链接")


def _validate_final_path(final_path: str, requested_path: Path) -> None:
    expected = ntpath.normcase(ntpath.abspath(str(requested_path)))
    actual = ntpath.normcase(ntpath.normpath(final_path))
    if actual != expected:
        raise ValueError("Worker 凭证文件句柄解析到非预期路径")


def _read_bounded(api: WindowsKernel, handle: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := api.read(handle, _READ_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Worker 凭证文件超过 64 KiB 上限")
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = ["read_private_windows_file"]
