"""Strict I/O and metadata validation for managed-runtime manifests."""

from __future__ import annotations

import os
from typing import Any

import ujson
from antcode_contracts.runtime_metadata import (
    RUNTIME_MANIFEST_MAX_BYTES,
    validate_runtime_creator,
    validate_runtime_metadata,
)


def load_runtime_manifest(path: str, *, required: bool = False) -> dict[str, Any]:
    if not os.path.exists(path):
        if required:
            raise RuntimeError(f"运行时清单文件不存在: {path}")
        return {}
    if os.path.getsize(path) > RUNTIME_MANIFEST_MAX_BYTES:
        raise RuntimeError(f"运行时清单文件超过 {RUNTIME_MANIFEST_MAX_BYTES} 字节上限: {path}")
    try:
        with open(path, encoding="utf-8") as file:
            manifest = ujson.load(file)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"运行时清单文件无效: {path}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"运行时清单根节点必须是 object: {path}")
    return manifest


def runtime_manifest_metadata(manifest: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        return validate_runtime_metadata(manifest.get("key"), manifest.get("description"))
    except ValueError as exc:
        raise RuntimeError("运行时清单 metadata 超出合同") from exc


def runtime_manifest_creator(manifest: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        return validate_runtime_creator(manifest.get("created_by"), manifest.get("owner_user_id"))
    except ValueError as exc:
        raise RuntimeError("运行时清单 creator metadata 超出合同") from exc


def write_runtime_manifest(path: str, manifest: dict[str, Any]) -> None:
    serialized = ujson.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    if len(serialized) > RUNTIME_MANIFEST_MAX_BYTES:
        raise RuntimeError(f"运行时清单超过 {RUNTIME_MANIFEST_MAX_BYTES} 字节上限")
    with open(path, "wb") as file:
        file.write(serialized)


__all__ = [
    "load_runtime_manifest",
    "runtime_manifest_creator",
    "runtime_manifest_metadata",
    "write_runtime_manifest",
]
