"""Worker 主进程中的 SpiderData spool relay。"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from antcode_worker.transport.base import TransportBase

SPIDER_RELAY_BATCH_SIZE = 50
_ITEM_FIELDS = frozenset(
    {
        "item_id",
        "run_id",
        "project_id",
        "spider_name",
        "item_type",
        "data",
        "url",
        "timestamp",
        "sequence",
    }
)


@dataclass(frozen=True)
class SpoolRecord:
    record_type: str
    payload: dict[str, Any]


async def relay_spider_spool(
    path: str,
    transport: TransportBase,
    *,
    expected_run_id: str,
    expected_project_id: str,
    batch_size: int = SPIDER_RELAY_BATCH_SIZE,
) -> None:
    """按原始顺序以有界批次转发 item/meta，失败直接抛出。"""
    if batch_size <= 0:
        raise ValueError("spider relay batch_size 必须大于 0")
    pending: list[dict[str, Any]] = []
    for record in _iter_records(path):
        _validate_context(record.payload, expected_run_id, expected_project_id)
        if record.record_type == "item":
            _validate_item(record.payload)
            pending.append(record.payload)
            if len(pending) >= batch_size:
                await _flush_items(transport, expected_run_id, pending)
                pending = []
            continue
        await _flush_items(transport, expected_run_id, pending)
        pending = []
        await _send_meta(transport, expected_run_id, record.payload)
    await _flush_items(transport, expected_run_id, pending)


def _iter_records(path: str) -> Iterator[SpoolRecord]:
    fd = _open_spool(path)
    with os.fdopen(fd, encoding="utf-8") as spool:
        for line_number, line in enumerate(spool, start=1):
            if not line.strip():
                continue
            yield _parse_record(line, line_number)


def _parse_record(line: str, line_number: int) -> SpoolRecord:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"spider spool 第 {line_number} 行 JSON 损坏") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("payload"), dict):
        raise RuntimeError(f"spider spool 第 {line_number} 行结构无效")
    record_type = raw.get("type")
    if record_type not in {"item", "meta"}:
        raise RuntimeError(f"spider spool 第 {line_number} 行类型无效: {record_type!r}")
    return SpoolRecord(record_type=record_type, payload=raw["payload"])


def _validate_context(payload: dict[str, Any], run_id: str, project_id: str) -> None:
    if payload.get("run_id") != run_id:
        raise RuntimeError("spider spool run_id 与当前任务不匹配")
    if payload.get("project_id") != project_id:
        raise RuntimeError("spider spool project_id 与当前任务不匹配")


def _validate_item(payload: dict[str, Any]) -> None:
    missing = _ITEM_FIELDS.difference(payload)
    if missing:
        raise RuntimeError(f"spider spool item 缺少字段: {sorted(missing)}")
    if not isinstance(payload["data"], str):
        raise RuntimeError("spider spool item.data 必须是 JSON 字符串")
    if isinstance(payload["sequence"], bool) or not isinstance(payload["sequence"], int) or payload["sequence"] < 0:
        raise RuntimeError("spider spool item.sequence 必须是非负整数")


def _open_spool(path: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        _validate_spool_fd(fd)
    except Exception:
        os.close(fd)
        raise
    return fd


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


async def _flush_items(
    transport: TransportBase,
    run_id: str,
    items: list[dict[str, Any]],
) -> None:
    if not items:
        return
    if not await transport.report_spider_data(run_id, list(items)):
        raise RuntimeError(f"SpiderData batch relay 失败: run_id={run_id} items={len(items)}")


async def _send_meta(
    transport: TransportBase,
    run_id: str,
    meta: dict[str, Any],
) -> None:
    if not await transport.update_spider_meta(run_id, dict(meta)):
        raise RuntimeError(f"SpiderData meta relay 失败: run_id={run_id}")


__all__ = ["SPIDER_RELAY_BATCH_SIZE", "relay_spider_spool"]
