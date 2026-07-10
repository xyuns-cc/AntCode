"""PostgreSQL source artifact chunk store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tortoise.transactions import in_transaction

from antcode_core.domain.models import SourceArtifact, SourceArtifactChunk

ARTIFACT_CHUNK_SIZE_BYTES = 1024 * 1024


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
        # P1-13: SourceArtifact + SourceArtifactChunk 必须同事务写入。否则:
        #   1) 并发同 hash 两个 get_or_none 都返回 None → 各自 create 撞唯一约束
        #   2) SourceArtifact.create 成功但 chunks bulk_create 失败 → chunk_count
        #      对不上,后续 read_blob 抛 "chunk 数量不一致"
        content_hash = hashlib.sha256(content).hexdigest()
        chunks = _split_chunks(content)
        async with in_transaction("default") as conn:
            artifact = await SourceArtifact.filter(content_hash=content_hash).using_db(conn).first()
            if artifact is None:
                try:
                    artifact = await self._create_artifact(
                        content_hash, content, media_type, chunks, metadata, conn=conn
                    )
                except Exception:
                    # 并发竞态:另一事务已写入,重查并复用
                    artifact = await SourceArtifact.filter(content_hash=content_hash).using_db(conn).first()
                    if artifact is None:
                        raise
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

    async def _create_artifact(
        self,
        content_hash: str,
        content: bytes,
        media_type: str,
        chunks: list[bytes],
        metadata: dict[str, object] | None,
        conn=None,
    ) -> SourceArtifact:
        # P1-13: 接受外部 conn,与 write_blob in_transaction 复用同事务
        values = _artifact_values(content_hash, content, media_type, chunks, metadata)
        artifact = await SourceArtifact.create(**values, using_db=conn)
        await SourceArtifactChunk.bulk_create(
            [
                SourceArtifactChunk(
                    artifact_id=artifact.id,
                    chunk_index=index,
                    content=chunk,
                )
                for index, chunk in enumerate(chunks)
            ],
            using_db=conn,
        )
        return artifact


def _artifact_values(
    content_hash: str,
    content: bytes,
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
