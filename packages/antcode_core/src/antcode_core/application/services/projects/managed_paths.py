"""后端托管项目产物路径工具。"""

from __future__ import annotations

import os
from pathlib import Path

from antcode_core.common.config import settings


MANAGED_PREFIX = "managed-projects"


def build_managed_path(*parts: str) -> str:
    normalized = [_normalize_part(part) for part in parts if part and str(part).strip()]
    return "/".join([MANAGED_PREFIX, *normalized])


def is_managed_path(path: str | None) -> bool:
    return bool(path and str(path).startswith(f"{MANAGED_PREFIX}/"))


def resolve_managed_path(path: str) -> str:
    if not is_managed_path(path):
        raise ValueError("不是托管项目路径")

    relative = path[len(MANAGED_PREFIX) + 1 :]
    target = (Path(get_managed_root()) / relative).resolve()
    root = Path(get_managed_root()).resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root):
        raise ValueError("托管项目路径越界")
    return str(target)


def get_managed_root() -> str:
    root = Path(settings.data_dir) / "storage" / MANAGED_PREFIX
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _normalize_part(value: str) -> str:
    normalized = str(value).strip().replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        raise ValueError("路径片段不能为空")
    if ".." in normalized.split("/"):
        raise ValueError("路径片段不合法")
    return normalized

