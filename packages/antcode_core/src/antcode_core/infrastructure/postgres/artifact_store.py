"""PostgreSQL source artifact chunk store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tortoise.transactions import in_transaction

from antcode_core.domain.models import SourceArtifact, SourceArtifactChunk
from antcode_core.domain.models.base import generate_public_id

ARTIFACT_CHUNK_SIZE_BYTES = 1024 * 1024

_INSERT_ARTIFACT_SQL = """
INSERT INTO source_artifacts (
    public_id,
    content_hash,
    media_type,
    size_bytes,
    chunk_count,
    repository_id,
    resolved_commit,
    source_subdir,
    include_paths_hash,
    created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, CURRENT_TIMESTAMP)
ON CONFLICT (content_hash) DO NOTHING
RETURNING id
"""


@dataclass(frozen=True)
class StoredArtifact:
    uri: str
    content_hash: str
    size_bytes: int
    media_type: str
    artifact_id: int
    chunk_count: int


class PostgresArtifactStore:
    """Content-addressed source artifact store backed by PostgreSQL chunks."""

    async def write_blob(
        self,
        content: bytes,
        media_type: str = "application/octet-stream",
        metadata: dict[str, object] | None = None,
    ) -> StoredArtifact:
        content_hash = hashlib.sha256(content).hexdigest()
        chunks = _split_chunks(content)
        values = _artifact_values(
            content_hash,
            content,
            media_type=media_type,
            chunks=chunks,
            metadata=metadata,
        )
        async with in_transaction("default") as conn:
            artifact_id = await _insert_artifact(values, conn)
            if artifact_id is not None:
                await _create_chunks(artifact_id, chunks, conn)
            artifact = await _get_artifact(content_hash, conn)
        return _stored_artifact(artifact)

    async def read_blob(self, content_hash: str) -> bytes:
        normalized = _normalize_sha256(content_hash)
        artifact = await SourceArtifact.get_or_none(content_hash=normalized)
        if artifact is None:
            raise FileNotFoundError(f"Artifact 不存在: {normalized}")
        chunks = await SourceArtifactChunk.filter(artifact_id=artifact.id).order_by("chunk_index")
        if len(chunks) != artifact.chunk_count:
            raise ValueError("Artifact chunk 数量不一致")
        content = b"".join(bytes(chunk.content) for chunk in chunks)
        _verify_content(content, artifact)
        return content


def _artifact_values(
    content_hash: str,
    content: bytes,
    *,
    media_type: str,
    chunks: list[bytes],
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "content_hash": content_hash,
        "media_type": media_type,
        "size_bytes": len(content),
        "chunk_count": len(chunks),
    }
    if metadata:
        values.update(
            {
                "repository_id": metadata.get("repository_id"),
                "resolved_commit": metadata.get("resolved_commit"),
                "source_subdir": metadata.get("source_subdir"),
                "include_paths_hash": metadata.get("include_paths_hash"),
            }
        )
    return values


async def _insert_artifact(values: dict[str, object], conn) -> int | None:
    params = [
        generate_public_id(),
        values["content_hash"],
        values["media_type"],
        values["size_bytes"],
        values["chunk_count"],
        values.get("repository_id"),
        values.get("resolved_commit"),
        values.get("source_subdir"),
        values.get("include_paths_hash"),
    ]
    rows = await conn.execute_query_dict(_INSERT_ARTIFACT_SQL, params)
    if not rows:
        return None
    return int(rows[0]["id"])


async def _create_chunks(artifact_id: int, chunks: list[bytes], conn) -> None:
    await SourceArtifactChunk.bulk_create(
        [
            SourceArtifactChunk(
                artifact_id=artifact_id,
                chunk_index=index,
                content=chunk,
            )
            for index, chunk in enumerate(chunks)
        ],
        using_db=conn,
    )


async def _get_artifact(content_hash: str, conn) -> SourceArtifact:
    artifact = await SourceArtifact.filter(content_hash=content_hash).using_db(conn).first()
    if artifact is None:
        raise RuntimeError(f"Artifact 写入后不存在: {content_hash}")
    return artifact


def _stored_artifact(artifact: SourceArtifact) -> StoredArtifact:
    return StoredArtifact(
        uri=f"pgartifact://{artifact.content_hash}",
        content_hash=artifact.content_hash,
        size_bytes=int(artifact.size_bytes),
        media_type=artifact.media_type,
        artifact_id=int(artifact.id),
        chunk_count=int(artifact.chunk_count),
    )


def _split_chunks(content: bytes) -> list[bytes]:
    if not content:
        return [b""]
    return [
        content[index : index + ARTIFACT_CHUNK_SIZE_BYTES]
        for index in range(0, len(content), ARTIFACT_CHUNK_SIZE_BYTES)
    ]


def _verify_content(content: bytes, artifact: SourceArtifact) -> None:
    if len(content) != artifact.size_bytes:
        raise ValueError("Artifact 大小不一致")
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != artifact.content_hash:
        raise ValueError("Artifact sha256 不一致")


def _normalize_sha256(value: str) -> str:
    normalized = (value or "").strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("Artifact hash 必须是 64 位 SHA256")
    return normalized
