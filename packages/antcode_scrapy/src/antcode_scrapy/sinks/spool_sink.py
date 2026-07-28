"""仅写本地 JSONL 的 SpiderData spool sink。"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from typing import Any


class SpoolSpiderDataSink:
    """把 item/meta 顺序追加到 Worker 创建的本地 spool。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._fd: int | None = None
        self._lock = asyncio.Lock()
        self._context: dict[str, str] = {}

    async def open(
        self,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        namespace: str,
    ) -> None:
        del namespace
        self._context = {
            "run_id": run_id,
            "project_id": project_id,
            "spider_name": spider_name,
        }
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self._path, flags)
        try:
            _validate_spool_fd(fd)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    async def write_item(
        self,
        *,
        item_id: str,
        item_type: str,
        data_json: str,
        url: str,
        timestamp: str,
        sequence: int,
    ) -> bool:
        payload: dict[str, Any] = {
            **self._context,
            "item_id": item_id,
            "item_type": item_type or "default",
            "data": data_json,
            "url": url,
            "timestamp": timestamp,
            "sequence": sequence,
        }
        await self._append("item", payload)
        return True

    async def write_meta(self, fields: dict[str, str]) -> None:
        payload = {**self._context, **{key: str(value) for key, value in fields.items()}}
        await self._append("meta", payload)

    async def close(self, final_meta: dict[str, str] | None = None) -> None:
        if final_meta:
            await self.write_meta(final_meta)
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    async def _append(self, record_type: str, payload: dict[str, Any]) -> None:
        if self._fd is None:
            raise RuntimeError("spool sink 未打开")
        encoded = (json.dumps({"type": record_type, "payload": payload}, ensure_ascii=False) + "\n").encode()
        async with self._lock:
            self._write_all(encoded)

    def _write_all(self, data: bytes) -> None:
        if self._fd is None:
            raise RuntimeError("spool sink 未打开")
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            if written <= 0:
                raise OSError("写入 spider spool 失败")
            view = view[written:]


def _validate_spool_fd(fd: int) -> None:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        raise PermissionError("spider spool 必须是普通文件")
    if file_stat.st_nlink != 1:
        raise PermissionError("spider spool 不允许硬链接")
    if os.name == "nt":
        return
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise PermissionError("spider spool 权限必须为 0600")
    if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
        raise PermissionError("spider spool 所有者不是当前进程用户")


__all__ = ["SpoolSpiderDataSink"]
