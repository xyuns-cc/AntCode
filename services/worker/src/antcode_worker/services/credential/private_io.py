"""Bounded file-descriptor IO for Worker secrets."""

from __future__ import annotations

import os


def read_limited(fd: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        chunk = os.read(fd, min(8192, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > max_bytes:
        raise ValueError("Worker 凭证文件超过 64 KiB 上限")
    return content


def write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("写入 Worker 凭证文件失败")
        view = view[written:]


__all__ = ["read_limited", "write_all"]
