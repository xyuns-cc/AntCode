"""Authoritative per-run workspace scope checks for task sandboxes."""

from __future__ import annotations

import tempfile
from pathlib import Path

_RUNS_DIRECTORY = "runs"
_SOURCES_DIRECTORY = "sources"
_MIN_WORKSPACE_COMPONENTS = 2
_BUNDLE_DIRECTORY = "extracted"
# ArtifactFetcher 的解包布局：<run_id>/<project_id>/<content_hash>/extracted
_BUNDLE_PATH_COMPONENTS = 4
_RULE_TEMP_ROOT = Path(tempfile.gettempdir()).resolve() / "antcode-rule"


def validate_workspace_scope(
    work_dir: Path,
    *,
    data_root: Path,
    runtimes_root: Path,
    plugin_name: str | None,
    run_id: str | None,
) -> None:
    """Require the writable bind to belong to the authoritative current run."""
    if _inside(work_dir, runtimes_root):
        raise RuntimeError("任务工作目录不能位于 Worker runtime 树")
    if _is_private_rule_workspace(work_dir, plugin_name=plugin_name, run_id=run_id):
        return
    if not _inside(work_dir, data_root):
        raise RuntimeError("任务工作目录不在 Worker 当前任务工作区")
    _require_run_id(run_id)
    relative = _relative_source_workspace(work_dir, data_root=data_root, label="任务工作目录")
    if len(relative.parts) < _MIN_WORKSPACE_COMPONENTS:
        raise RuntimeError("任务工作目录范围过宽，必须位于单个 run 的 source workspace")
    if relative.parts[0] != run_id:
        raise RuntimeError("任务工作目录不属于当前 run")


def validate_bundle_root_scope(
    bundle_root: Path,
    *,
    work_dir: Path,
    data_root: Path,
    run_id: str | None,
) -> None:
    """Only the current run's own extracted source bundle may be exposed read-only.

    include_paths 目录是 bundle 内 work_dir 的兄弟节点，暴露它必须以「解包根目录」为
    单位；这里把可暴露范围钉死成 ArtifactFetcher 生成的那一个 extracted 目录，越界、
    层级不符或不是 work_dir 上级的路径一律拒绝。
    """
    _require_run_id(run_id)
    relative = _relative_source_workspace(bundle_root, data_root=data_root, label="source bundle 根目录")
    if len(relative.parts) != _BUNDLE_PATH_COMPONENTS or relative.parts[-1] != _BUNDLE_DIRECTORY:
        raise RuntimeError(f"source bundle 根目录不是 run 的解包目录: {bundle_root}")
    if relative.parts[0] != run_id:
        raise RuntimeError("source bundle 根目录不属于当前 run")
    if bundle_root not in work_dir.parents:
        raise RuntimeError("source bundle 根目录必须是任务工作目录的上级")


def _relative_source_workspace(path: Path, *, data_root: Path, label: str) -> Path:
    allowed_root = data_root / _RUNS_DIRECTORY / _SOURCES_DIRECTORY
    try:
        return path.relative_to(allowed_root)
    except ValueError as exc:
        raise RuntimeError(f"{label}不在当前 run 的 source workspace") from exc


def _is_private_rule_workspace(work_dir: Path, *, plugin_name: str | None, run_id: str | None) -> bool:
    if plugin_name != "rule" or run_id is None or not _valid_run_id(run_id):
        return False
    return work_dir == (_RULE_TEMP_ROOT / run_id).resolve()


def _require_run_id(run_id: str | None) -> None:
    if not _valid_run_id(run_id):
        raise RuntimeError("sandbox 缺少合法的当前 run_id")


def _valid_run_id(run_id: str | None) -> bool:
    return bool(run_id and run_id not in {".", ".."} and Path(run_id).name == run_id)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = ["validate_bundle_root_scope", "validate_workspace_scope"]
