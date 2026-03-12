"""项目来源辅助工具。"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse


SOURCE_TYPE_INLINE = "inline"
SOURCE_TYPE_LEGACY_INLINE = "legacy_inline"
SOURCE_TYPE_S3 = "s3"
SOURCE_TYPE_GIT = "git"
SOURCE_CONFIG_KEY = "source"
ARTIFACT_CONFIG_KEY = "artifact"
ARTIFACT_STORAGE_MANAGED = "managed"

_SOURCE_ALIASES = {
    "upload": SOURCE_TYPE_S3,
    SOURCE_TYPE_S3: SOURCE_TYPE_S3,
    SOURCE_TYPE_GIT: SOURCE_TYPE_GIT,
    SOURCE_TYPE_INLINE: SOURCE_TYPE_LEGACY_INLINE,
    SOURCE_TYPE_LEGACY_INLINE: SOURCE_TYPE_LEGACY_INLINE,
}


def normalize_source_type(value: str | None, default: str) -> str:
    normalized = _SOURCE_ALIASES.get((value or default).strip().lower())
    if normalized not in {SOURCE_TYPE_LEGACY_INLINE, SOURCE_TYPE_S3, SOURCE_TYPE_GIT}:
        raise ValueError(f"不支持的代码来源类型: {value}")
    return normalized


def normalize_code_source_type(value: str | None) -> str:
    return normalize_source_type(value, SOURCE_TYPE_INLINE)


def normalize_file_source_type(value: str | None) -> str:
    return normalize_source_type(value, SOURCE_TYPE_S3)


def build_code_source_config(
    source_type: str | None,
    git_url: str | None = None,
    git_branch: str | None = None,
    git_commit: str | None = None,
    git_subdir: str | None = None,
    git_credential_id: str | None = None,
    s3_key: str | None = None,
    original_name: str | None = None,
    is_compressed: bool | None = None,
    entry_point: str | None = None,
) -> dict[str, str | bool]:
    normalized = normalize_source_type(source_type, SOURCE_TYPE_INLINE)
    if normalized == SOURCE_TYPE_LEGACY_INLINE:
        return {"type": SOURCE_TYPE_LEGACY_INLINE}
    if normalized == SOURCE_TYPE_S3:
        return _build_s3_source_config(s3_key, original_name, is_compressed, entry_point)
    return _build_git_source_config(
        git_url,
        git_branch,
        git_commit,
        git_subdir,
        git_credential_id,
    )


def merge_code_source_runtime_config(
    runtime_config: dict[str, Any] | None,
    source_config: dict[str, str | bool],
) -> dict[str, Any]:
    merged = dict(runtime_config or {})
    merged[SOURCE_CONFIG_KEY] = dict(source_config)
    return merged


def merge_artifact_runtime_config(
    runtime_config: dict[str, Any] | None,
    artifact_config: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(runtime_config or {})
    merged[ARTIFACT_CONFIG_KEY] = dict(artifact_config)
    return merged


def build_artifact_config(
    *,
    storage_type: str,
    file_path: str,
    original_file_path: str | None,
    original_name: str,
    file_size: int,
    file_hash: str,
    file_type: str,
    is_compressed: bool,
    entry_point: str | None,
    resolved_revision: str | None = None,
) -> dict[str, Any]:
    return {
        "storage_type": storage_type,
        "file_path": file_path,
        "original_file_path": original_file_path or "",
        "original_name": original_name,
        "file_size": file_size,
        "file_hash": file_hash,
        "file_type": file_type,
        "is_compressed": bool(is_compressed),
        "entry_point": entry_point or "",
        "resolved_revision": resolved_revision or "",
    }


def get_runtime_source_config(detail: Any) -> dict[str, str | bool]:
    runtime_config = getattr(detail, "runtime_config", None) or {}
    source = runtime_config.get(SOURCE_CONFIG_KEY)
    if not isinstance(source, dict):
        return _default_source_config(detail)
    return build_code_source_config(
        source_type=source.get("type"),
        git_url=source.get("url"),
        git_branch=source.get("branch"),
        git_commit=source.get("commit"),
        git_subdir=source.get("subdir"),
        git_credential_id=source.get("git_credential_id"),
        s3_key=source.get("s3_key"),
        original_name=source.get("original_name"),
        is_compressed=source.get("is_compressed"),
        entry_point=source.get("entry_point"),
    )


def get_runtime_artifact_config(detail: Any) -> dict[str, Any]:
    runtime_config = getattr(detail, "runtime_config", None) or {}
    artifact = runtime_config.get(ARTIFACT_CONFIG_KEY)
    return dict(artifact) if isinstance(artifact, dict) else {}


def get_code_source_config(code_detail: Any) -> dict[str, str | bool]:
    return get_runtime_source_config(code_detail)


def build_git_download_url(source_config: dict[str, str]) -> str:
    if source_config.get("type") != SOURCE_TYPE_GIT:
        raise ValueError("仅支持为 Git 来源构建下载地址")
    repo_url = _normalize_git_url(source_config.get("url"))
    params: dict[str, str] = {}
    if source_config.get("branch"):
        params["ref"] = source_config["branch"]
    if source_config.get("commit"):
        params["commit"] = source_config["commit"]
    if source_config.get("subdir"):
        params["subdir"] = source_config["subdir"]
    query = urlencode(params, doseq=False, safe="/:@")
    separator = "&" if "?" in repo_url else "?"
    return f"git+{repo_url}{separator}{query}" if query else f"git+{repo_url}"


def parse_git_download_url(download_url: str) -> dict[str, str]:
    if not download_url.startswith("git+"):
        raise ValueError("不是 Git 下载地址")
    raw_url = download_url[4:]
    parsed = urlparse(raw_url)
    if not parsed.scheme:
        raise ValueError("Git 地址必须包含协议")
    query = parse_qs(parsed.query)
    repo_url = parsed._replace(query="", fragment="").geturl()
    config = {"type": SOURCE_TYPE_GIT, "url": repo_url}
    if ref := _get_first_query_value(query, "ref"):
        config["branch"] = ref
    if commit := _get_first_query_value(query, "commit"):
        config["commit"] = commit
    if subdir := _get_first_query_value(query, "subdir"):
        config["subdir"] = normalize_git_subdir(subdir)
    return config


def normalize_git_subdir(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace("\\", "/").strip("/")
    if not normalized:
        return None
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        raise ValueError("git_subdir 不合法")
    return "/".join(parts)


def _build_s3_source_config(
    s3_key: str | None,
    original_name: str | None,
    is_compressed: bool | None,
    entry_point: str | None,
) -> dict[str, str | bool]:
    config: dict[str, str | bool] = {"type": SOURCE_TYPE_S3}
    if s3_key and s3_key.strip():
        config["s3_key"] = s3_key.strip()
    if original_name and original_name.strip():
        config["original_name"] = original_name.strip()
    if is_compressed is not None:
        config["is_compressed"] = bool(is_compressed)
    if entry_point and str(entry_point).strip():
        config["entry_point"] = str(entry_point).strip()
    return config


def _build_git_source_config(
    git_url: str | None,
    git_branch: str | None,
    git_commit: str | None,
    git_subdir: str | None,
    git_credential_id: str | None,
) -> dict[str, str]:
    repo_url = _normalize_git_url(git_url)
    config = {"type": SOURCE_TYPE_GIT, "url": repo_url}
    if git_branch and git_branch.strip():
        config["branch"] = git_branch.strip()
    if git_commit and git_commit.strip():
        config["commit"] = git_commit.strip()
    subdir = normalize_git_subdir(git_subdir)
    if subdir:
        config["subdir"] = subdir
    if git_credential_id and git_credential_id.strip():
        config["git_credential_id"] = git_credential_id.strip()
    return config


def _default_source_config(detail: Any) -> dict[str, str]:
    if getattr(detail, "content", None) not in {None, ""}:
        return {"type": SOURCE_TYPE_LEGACY_INLINE}
    return {"type": SOURCE_TYPE_S3}


def _normalize_git_url(value: str | None) -> str:
    repo_url = (value or "").strip()
    if not repo_url:
        raise ValueError("git_url 不能为空")
    parsed = urlparse(repo_url)
    if parsed.scheme not in {"https", "http", "ssh", "file"}:
        raise ValueError("git_url 必须使用 https/http/ssh/file 协议")
    return repo_url


def _get_first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None
