"""项目存储路径判定工具。"""

from __future__ import annotations

from antcode_core.application.services.projects.managed_paths import is_managed_path


_S3_STORAGE_PREFIXES = ("projects/", "files/", "temp/")


def is_s3_storage_key(path: str | None) -> bool:
    normalized = str(path or "").strip()
    if not normalized or is_managed_path(normalized):
        return False
    return normalized.startswith(_S3_STORAGE_PREFIXES)
