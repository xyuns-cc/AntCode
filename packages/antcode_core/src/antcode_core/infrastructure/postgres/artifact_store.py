"""PostgreSQL source artifact chunk store."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import aiofiles  # type: ignore[import-untyped]
from tortoise.transactions import in_transaction

from antcode_core.domain.models import SourceArtifact, SourceArtifactChunk
from antcode_core.domain.models.base import generate_public_id

ARTIFACT_CHUNK_SIZE_BYTES = 1024 * 1024
ARTIFACT_CURSOR_PREFETCH = 8

_SELECT_CHUNKS_SQL = """
SELECT chunk_index, content
FROM source_artifact_chunks
WHERE artifact_id = $1
ORDER BY chunk_index
"""

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

    async def write_blob_from_file(
        self,
        source: Path,
        media_type: str = "application/octet-stream",
        metadata: dict[str, object] | None = None,
    ) -> StoredArtifact:
        """流式写入：两遍顺序读文件（先哈希/大小，再分块入库）。

        P2 §4.2: 上传路径此前 ``read_bytes()`` 整读 + ``write_blob`` 内再
        切片，单流可占用数倍于文件大小的内存；本方法内存占用 O(chunk)。
        两遍之间文件被改动会因哈希不一致显式失败。
        """
        content_hash, size_bytes = await _hash_file(source)
        chunk_count = max(1, -(-size_bytes // ARTIFACT_CHUNK_SIZE_BYTES)) if size_bytes else 1
        values: dict[str, object] = {
            "content_hash": content_hash,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "chunk_count": chunk_count,
        }
        values.update(_metadata_values(metadata))
        async with in_transaction("default") as conn:
            artifact_id = await _insert_artifact(values, conn)
            if artifact_id is not None:
                await _create_chunks_from_file(artifact_id, source, expected_hash=content_hash, conn=conn)
            artifact = await _get_artifact(content_hash, conn)
        return _stored_artifact(artifact)

    async def read_blob(self, content_hash: str) -> bytes:
        normalized = _normalize_sha256(content_hash)
        artifact = await SourceArtifact.get_or_none(content_hash=normalized)
        if artifact is None:
            raise FileNotFoundError(f"Artifact 不存在: {normalized}")
        chunks = [chunk async for chunk in _iter_chunk_contents(artifact.id, artifact.chunk_count)]
        content = b"".join(chunks)
        _verify_content(content, artifact)
        return content

    async def stat_blob(self, content_hash: str) -> StoredArtifact:
        """Validate metadata and chunk count without loading chunk content."""
        normalized = _normalize_sha256(content_hash)
        artifact = await SourceArtifact.get_or_none(content_hash=normalized)
        if artifact is None:
            raise FileNotFoundError(f"Artifact 不存在: {normalized}")
        chunk_count = await SourceArtifactChunk.filter(artifact_id=artifact.id).count()
        if chunk_count != artifact.chunk_count:
            raise ValueError("Artifact chunk 数量不一致")
        return _stored_artifact(artifact)

    async def iter_blob(self, content_hash: str) -> AsyncIterator[bytes]:
        """Yield ordered chunks and verify size/hash without assembling the blob."""
        normalized = _normalize_sha256(content_hash)
        artifact = await SourceArtifact.get_or_none(content_hash=normalized)
        if artifact is None:
            raise FileNotFoundError(f"Artifact 不存在: {normalized}")
        digest = hashlib.sha256()
        size_bytes = 0
        async for content in _iter_chunk_contents(artifact.id, artifact.chunk_count):
            digest.update(content)
            size_bytes += len(content)
            yield content
        _verify_streamed_content(size_bytes, digest.hexdigest(), artifact)

    async def read_blob_to_file(
        self,
        content_hash: str,
        destination: Path,
        *,
        max_bytes: int | None = None,
    ) -> StoredArtifact:
        """Stream ordered chunks to disk without assembling the artifact in memory."""
        normalized = _normalize_sha256(content_hash)
        artifact = await SourceArtifact.get_or_none(content_hash=normalized)
        if artifact is None:
            raise FileNotFoundError(f"Artifact 不存在: {normalized}")
        if max_bytes is not None and artifact.size_bytes > max_bytes:
            raise ValueError(f"Artifact 超过大小上限: {artifact.size_bytes} > {max_bytes}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        try:
            async with aiofiles.open(destination, "wb") as output:
                async for content in _iter_chunk_contents(artifact.id, artifact.chunk_count):
                    written += len(content)
                    if max_bytes is not None and written > max_bytes:
                        raise ValueError(f"Artifact 超过大小上限: {written} > {max_bytes}")
                    digest.update(content)
                    await output.write(content)
            _verify_streamed_content(written, digest.hexdigest(), artifact)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        return _stored_artifact(artifact)


async def _iter_chunk_contents(artifact_id: int, expected_count: int) -> AsyncIterator[bytes]:
    """Read all ordered chunks through one asyncpg server-side cursor."""
    async with in_transaction("default") as conn:
        raw_connection = getattr(conn, "_connection", None)
        if raw_connection is None:
            raise RuntimeError("PostgreSQL transaction 缺少底层 asyncpg connection")
        cursor = raw_connection.cursor(
            _SELECT_CHUNKS_SQL,
            artifact_id,
            prefetch=ARTIFACT_CURSOR_PREFETCH,
        )
        count = 0
        async for row in cursor:
            chunk_index = int(row["chunk_index"])
            if chunk_index != count:
                raise ValueError(f"Artifact chunk 缺失或乱序: expected={count} actual={chunk_index}")
            count += 1
            yield bytes(row["content"])
        if count != expected_count:
            raise ValueError(f"Artifact chunk 数量不一致: expected={expected_count} actual={count}")


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
    values.update(_metadata_values(metadata))
    return values


def _metadata_values(metadata: dict[str, object] | None) -> dict[str, object]:
    if not metadata:
        return {}
    return {
        "repository_id": metadata.get("repository_id"),
        "resolved_commit": metadata.get("resolved_commit"),
        "source_subdir": metadata.get("source_subdir"),
        "include_paths_hash": metadata.get("include_paths_hash"),
    }


async def _hash_file(source: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    async with aiofiles.open(source, "rb") as handle:
        while data := await handle.read(ARTIFACT_CHUNK_SIZE_BYTES):
            digest.update(data)
            size_bytes += len(data)
    return digest.hexdigest(), size_bytes


# 每批插入的 chunk 数：限制流式写入的驻留内存 = 批大小 × chunk 大小。
_CHUNK_INSERT_BATCH = 8


async def _create_chunks_from_file(artifact_id: int, source: Path, *, expected_hash: str, conn) -> None:
    digest = hashlib.sha256()
    batch: list[SourceArtifactChunk] = []
    index = 0
    async with aiofiles.open(source, "rb") as handle:
        while data := await handle.read(ARTIFACT_CHUNK_SIZE_BYTES):
            digest.update(data)
            batch.append(SourceArtifactChunk(artifact_id=artifact_id, chunk_index=index, content=data))
            index += 1
            if len(batch) >= _CHUNK_INSERT_BATCH:
                await SourceArtifactChunk.bulk_create(batch, using_db=conn)
                batch = []
    if index == 0:
        batch.append(SourceArtifactChunk(artifact_id=artifact_id, chunk_index=0, content=b""))
    if batch:
        await SourceArtifactChunk.bulk_create(batch, using_db=conn)
    if index > 0 and digest.hexdigest() != expected_hash:
        raise ValueError("Artifact 源文件在写入期间发生变化（哈希不一致）")


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


def _verify_streamed_content(size_bytes: int, content_hash: str, artifact: SourceArtifact) -> None:
    if size_bytes != artifact.size_bytes:
        raise ValueError("Artifact 大小不一致")
    if content_hash != artifact.content_hash:
        raise ValueError("Artifact sha256 不一致")


def _normalize_sha256(value: str) -> str:
    normalized = (value or "").strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("Artifact hash 必须是 64 位 SHA256")
    return normalized
