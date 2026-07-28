"""Compatibility helpers for artifact callers."""

from __future__ import annotations

from pathlib import Path

from antcode_worker.domain.models import ArtifactRef
from antcode_worker.executor.artifact_collector import ArtifactCollector, ArtifactCollectorConfig
from antcode_worker.executor.artifact_io import hash_regular_file, inspect_candidate, inspect_work_root


async def collect_artifacts(
    work_dir: str,
    patterns: list[str],
    run_id: str | None = None,
    *,
    config: ArtifactCollectorConfig | None = None,
) -> list[ArtifactRef]:
    collector = ArtifactCollector(config)
    result = await collector.collect(work_dir, patterns, run_id)
    return result.artifacts


def compute_file_checksum(file_path: str, algorithm: str = "sha256") -> str:
    path = Path(file_path)
    root = inspect_work_root(path.parent)
    identity = inspect_candidate(path, path.name)
    if identity is None:
        raise ValueError(f"产物校验对象必须是文件: {file_path}")
    return hash_regular_file(
        root.path,
        path.name,
        expected_root=root.identity,
        expected=identity,
        algorithm=algorithm,
        max_bytes=identity.size,
    ).checksum


__all__ = ["collect_artifacts", "compute_file_checksum"]
