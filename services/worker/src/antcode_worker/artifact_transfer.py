"""Transport-neutral artifact persistence contracts for Worker execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class SourceBundleDownload:
    run_id: str
    project_id: str
    content_hash: str
    size_bytes: int
    max_bytes: int


@dataclass(frozen=True)
class TaskArtifactUpload:
    run_id: str
    name: str
    media_type: str
    content: bytes
    content_hash: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class StoredArtifact:
    uri: str
    content_hash: str
    size_bytes: int
    media_type: str


class ArtifactTransferStore(Protocol):
    async def download_source_bundle(
        self,
        request: SourceBundleDownload,
        destination: Path,
    ) -> StoredArtifact: ...

    async def upload_task_artifact(
        self,
        request: TaskArtifactUpload,
    ) -> StoredArtifact: ...


class PostgresArtifactTransferStore:
    """Direct-mode adapter around the PostgreSQL artifact chunk store."""

    def __init__(self, store: Any | None = None):
        if store is None:
            from antcode_core.infrastructure.postgres.artifact_store import (
                PostgresArtifactStore,
            )

            store = PostgresArtifactStore()
        self._store = store

    async def download_source_bundle(
        self,
        request: SourceBundleDownload,
        destination: Path,
    ) -> StoredArtifact:
        result = await self._store.read_blob_to_file(
            request.content_hash,
            destination,
            max_bytes=request.max_bytes,
        )
        return StoredArtifact(
            uri=result.uri,
            content_hash=result.content_hash,
            size_bytes=result.size_bytes,
            media_type=result.media_type,
        )

    async def upload_task_artifact(
        self,
        request: TaskArtifactUpload,
    ) -> StoredArtifact:
        result = await self._store.write_blob(
            request.content,
            media_type=request.media_type,
        )
        return StoredArtifact(
            uri=result.uri,
            content_hash=result.content_hash,
            size_bytes=result.size_bytes,
            media_type=result.media_type,
        )


__all__ = [
    "ArtifactTransferStore",
    "PostgresArtifactTransferStore",
    "SourceBundleDownload",
    "StoredArtifact",
    "TaskArtifactUpload",
]
