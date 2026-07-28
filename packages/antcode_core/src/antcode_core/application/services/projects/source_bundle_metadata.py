"""Metadata and scalar normalization for source bundle creation."""

from __future__ import annotations

import hashlib
import json

from antcode_core.application.services.projects.source_bundle_paths import string_list


def artifact_metadata(
    source_config: dict[str, object],
    resolved_revision: str,
) -> dict[str, object]:
    include_paths = string_list(source_config.get("include_paths"))
    return {
        "repository_id": source_config.get("repository_id"),
        "resolved_commit": resolved_revision,
        "source_subdir": source_config.get("subdir"),
        "include_paths_hash": _hash_json(include_paths),
    }


def _hash_json(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def require_git_source(source_config: dict[str, object]) -> None:
    if not source_config.get("url"):
        raise ValueError("Git URL 不能为空")


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["artifact_metadata", "optional_str", "require_git_source"]
