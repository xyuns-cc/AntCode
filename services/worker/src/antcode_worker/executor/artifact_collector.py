"""Artifact discovery and metadata collection."""

from __future__ import annotations

import asyncio
import fnmatch
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from antcode_worker.domain.enums import ArtifactType
from antcode_worker.domain.models import ArtifactRef
from antcode_worker.executor.artifact_io import (
    ArtifactFileIdentity,
    ArtifactSecurityError,
    VerifiedArtifactRoot,
    hash_regular_file,
    inspect_candidate,
    inspect_work_root,
    validate_artifact_pattern,
)

MEBIBYTE = 1024 * 1024
DEFAULT_MAX_FILE_SIZE = 100 * MEBIBYTE
DEFAULT_MAX_TOTAL_SIZE = 500 * MEBIBYTE
DEFAULT_MAX_FILE_COUNT = 1000
DEFAULT_MAX_CANDIDATE_COUNT = 2000
REPORT_MARKERS = (".html", ".pdf", ".md", "report", "summary")
DATA_MARKERS = (".json", ".csv", ".xml", ".yaml", ".yml", ".parquet")
ARCHIVE_MARKERS = (".zip", ".tar", ".gz", ".bz2", ".xz", ".7z")


@dataclass(frozen=True)
class ArtifactCollectorConfig:
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE
    max_file_count: int = DEFAULT_MAX_FILE_COUNT
    max_candidate_count: int = DEFAULT_MAX_CANDIDATE_COUNT
    compute_checksum: bool = True
    checksum_algorithm: str = "sha256"
    detect_mime_type: bool = True
    exclude_patterns: tuple[str, ...] = (
        "*.pyc",
        "__pycache__/*",
        ".git/*",
        ".venv/*",
        "*.egg-info/*",
        ".pytest_cache/*",
        ".mypy_cache/*",
        ".ruff_cache/*",
    )


@dataclass
class CollectionResult:
    artifacts: list[ArtifactRef]
    total_size: int
    file_count: int
    skipped_count: int
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "total_size": self.total_size,
            "file_count": self.file_count,
            "skipped_count": self.skipped_count,
            "errors": self.errors,
        }


@dataclass
class CollectedArtifactRef(ArtifactRef):
    collection_root: str = field(default="", repr=False)
    collection_root_identity: ArtifactFileIdentity | None = field(default=None, repr=False)
    collected_identity: ArtifactFileIdentity | None = field(default=None, repr=False)
    verification_checksum: str | None = field(default=None, repr=False)
    verification_algorithm: str = field(default="sha256", repr=False)


@dataclass
class _CollectionState:
    artifacts: list[ArtifactRef] = field(default_factory=list)
    total_size: int = 0
    skipped_count: int = 0
    errors: list[str] = field(default_factory=list)

    def result(self) -> CollectionResult:
        return CollectionResult(
            artifacts=self.artifacts,
            total_size=self.total_size,
            file_count=len(self.artifacts),
            skipped_count=self.skipped_count,
            errors=self.errors,
        )


class ArtifactCollector:
    """Collect regular output files beneath one immutable work root."""

    def __init__(self, config: ArtifactCollectorConfig | None = None):
        self.config = config or ArtifactCollectorConfig()

    async def collect(
        self,
        work_dir: str,
        patterns: list[str],
        run_id: str | None = None,
    ) -> CollectionResult:
        if not patterns:
            return _CollectionState().result()
        work_root = inspect_work_root(Path(work_dir))
        candidates = self._match_candidates(work_root.path, patterns)
        result = await self._collect_candidates(work_root, candidates)
        logger.debug(
            f"产物收集完成: run_id={run_id}, files={result.file_count}, "
            f"size={result.total_size}, skipped={result.skipped_count}"
        )
        return result

    def _match_candidates(
        self,
        work_path: Path,
        patterns: list[str],
    ) -> dict[str, ArtifactFileIdentity]:
        candidates: dict[str, ArtifactFileIdentity] = {}
        scanned_count = 0
        for pattern in patterns:
            validate_artifact_pattern(pattern)
            for file_path in work_path.glob(pattern):
                scanned_count += 1
                if scanned_count > self.config.max_candidate_count:
                    raise ArtifactSecurityError(f"产物候选数量超过上限: {self.config.max_candidate_count}")
                rel_path = file_path.relative_to(work_path).as_posix()
                identity = inspect_candidate(file_path, rel_path)
                if identity is not None:
                    candidates[rel_path] = identity
        return candidates

    async def _collect_candidates(
        self,
        work_root: VerifiedArtifactRoot,
        candidates: dict[str, ArtifactFileIdentity],
    ) -> CollectionResult:
        state = _CollectionState()
        for rel_path, identity in sorted(candidates.items()):
            if self._should_exclude(rel_path):
                state.skipped_count += 1
                continue
            if len(state.artifacts) >= self.config.max_file_count:
                state.skipped_count += 1
                state.errors.append(f"文件数量超限: {self.config.max_file_count}")
                break
            if identity.size > self.config.max_file_size:
                state.skipped_count += 1
                state.errors.append(f"文件过大: {rel_path} ({identity.size} > {self.config.max_file_size})")
                continue
            artifact = await self._create_artifact_ref(work_root, rel_path, identity=identity)
            if state.total_size + artifact.size_bytes > self.config.max_total_size:
                state.skipped_count += 1
                state.errors.append(f"总大小超限: {self.config.max_total_size}")
                break
            state.artifacts.append(artifact)
            state.total_size += artifact.size_bytes
        return state.result()

    async def _create_artifact_ref(
        self,
        work_root: VerifiedArtifactRoot,
        rel_path: str,
        *,
        identity: ArtifactFileIdentity,
    ) -> ArtifactRef:
        algorithm = self.config.checksum_algorithm if self.config.compute_checksum else "sha256"
        verified = await asyncio.to_thread(
            hash_regular_file,
            work_root.path,
            rel_path,
            expected_root=work_root.identity,
            expected=identity,
            algorithm=algorithm,
            max_bytes=self.config.max_file_size,
        )
        checksum = verified.checksum if self.config.compute_checksum else None
        mime_type = self._detect_mime_type(rel_path) if self.config.detect_mime_type else None
        return CollectedArtifactRef(
            name=rel_path,
            artifact_type=self._detect_artifact_type(rel_path),
            local_path=str(work_root.path / rel_path),
            size_bytes=verified.identity.size,
            checksum=checksum,
            mime_type=mime_type,
            created_at=datetime.now(),
            collection_root=str(work_root.path),
            collection_root_identity=work_root.identity,
            collected_identity=verified.identity,
            verification_checksum=verified.checksum,
            verification_algorithm=algorithm,
        )

    def _should_exclude(self, rel_path: str) -> bool:
        return any(fnmatch.fnmatch(rel_path, pattern) for pattern in self.config.exclude_patterns)

    def _detect_artifact_type(self, rel_path: str) -> ArtifactType:
        lower_path = rel_path.lower()
        if lower_path.endswith(".log") or "log" in lower_path:
            return ArtifactType.LOG
        if any(marker in lower_path for marker in REPORT_MARKERS):
            return ArtifactType.REPORT
        if any(marker in lower_path for marker in DATA_MARKERS):
            return ArtifactType.DATA
        if any(marker in lower_path for marker in ARCHIVE_MARKERS):
            return ArtifactType.ARCHIVE
        return ArtifactType.FILE

    def _detect_mime_type(self, rel_path: str) -> str | None:
        mime_type, _ = mimetypes.guess_type(rel_path)
        return mime_type


__all__ = [
    "ArtifactCollector",
    "ArtifactCollectorConfig",
    "CollectedArtifactRef",
    "CollectionResult",
]
