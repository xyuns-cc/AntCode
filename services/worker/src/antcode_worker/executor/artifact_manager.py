"""Artifact persistence after secure collection."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from antcode_worker.artifact_transfer import TaskArtifactUpload
from antcode_worker.domain.models import ArtifactRef
from antcode_worker.executor.artifact_collector import (
    ArtifactCollector,
    CollectedArtifactRef,
    CollectionResult,
)
from antcode_worker.executor.artifact_io import ArtifactSecurityError, read_verified_regular_file


class ArtifactManager:
    """Collect, verify, and persist task artifacts."""

    def __init__(
        self,
        collector: ArtifactCollector | None = None,
        artifact_store: Any | None = None,
    ):
        if artifact_store is None:
            raise ValueError("artifact_store 必须显式注入")
        self._collector = collector or ArtifactCollector()
        self._artifact_store = artifact_store

    @property
    def collector(self) -> ArtifactCollector:
        return self._collector

    async def collect_artifacts(
        self,
        work_dir: str,
        patterns: list[str],
        run_id: str | None = None,
    ) -> CollectionResult:
        return await self._collector.collect(work_dir, patterns, run_id)

    async def store_artifact(self, artifact: ArtifactRef, run_id: str) -> ArtifactRef:
        if not isinstance(artifact, CollectedArtifactRef):
            raise ArtifactSecurityError("仅允许存储由 ArtifactCollector 验证的产物")
        if not artifact.local_path:
            raise ValueError("artifact.local_path is required")
        content = await self._read_artifact_content(artifact)
        media_type = artifact.mime_type or self._guess_media_type(artifact.name)
        stored = await self._artifact_store.upload_task_artifact(
            TaskArtifactUpload(
                run_id=run_id,
                name=artifact.name,
                media_type=media_type,
                content=content,
                content_hash=hashlib.sha256(content).hexdigest(),
            )
        )
        artifact.uri = stored.uri
        artifact.local_path = None
        artifact.checksum = stored.content_hash
        artifact.size_bytes = stored.size_bytes
        artifact.mime_type = media_type
        logger.debug(f"产物已持久化: {artifact.name} -> {stored.uri}")
        return artifact

    async def cleanup_artifacts(self, run_id: str) -> None:
        raise RuntimeError("PostgreSQL artifact cleanup is handled by retention policy")

    async def get_artifact(self, run_id: str, artifact_name: str) -> ArtifactRef | None:
        raise RuntimeError("Artifacts are addressed by pgartifact URI")

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        raise RuntimeError("Artifacts are listed from task result metadata")

    async def _read_artifact_content(self, artifact: ArtifactRef) -> bytes:
        if not isinstance(artifact, CollectedArtifactRef):
            raise ArtifactSecurityError("仅允许读取由 ArtifactCollector 验证的产物")
        return await self._read_collected_artifact(artifact)

    async def _read_collected_artifact(self, artifact: CollectedArtifactRef) -> bytes:
        if (
            artifact.collection_root_identity is None
            or artifact.collected_identity is None
            or artifact.verification_checksum is None
        ):
            raise ArtifactSecurityError(f"产物缺少收集时安全快照: {artifact.name}")
        expected_path = Path(artifact.collection_root) / artifact.name
        if Path(artifact.local_path or "") != expected_path:
            raise ArtifactSecurityError(f"产物本地路径在收集后被篡改: {artifact.name}")
        return await asyncio.to_thread(
            read_verified_regular_file,
            Path(artifact.collection_root),
            artifact.name,
            expected_root=artifact.collection_root_identity,
            expected=artifact.collected_identity,
            expected_checksum=artifact.verification_checksum,
            algorithm=artifact.verification_algorithm,
            max_bytes=artifact.collected_identity.size,
        )

    def _guess_media_type(self, name: str) -> str:
        return mimetypes.guess_type(name)[0] or "application/octet-stream"


__all__ = ["ArtifactManager"]
