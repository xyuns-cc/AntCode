import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.domain.models import SourceArtifact, SourceArtifactChunk
from antcode_core.infrastructure.postgres.artifact_store import (
    ARTIFACT_CHUNK_SIZE_BYTES,
    PostgresArtifactStore,
    _split_chunks,
)


class _TransactionContext:
    def __init__(self, connection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _artifact(content_hash: str, artifact_id: int = 42):
    return SimpleNamespace(
        id=artifact_id,
        content_hash=content_hash,
        size_bytes=7,
        media_type="application/octet-stream",
        chunk_count=1,
    )


def _mock_artifact_lookup(artifact):
    queryset = MagicMock()
    queryset.using_db.return_value.first = AsyncMock(return_value=artifact)
    return queryset


def test_source_artifact_chunk_model_contract():
    artifact_fields = SourceArtifact._meta.fields_map
    chunk_fields = SourceArtifactChunk._meta.fields_map

    assert "content" not in artifact_fields
    assert {"content_hash", "media_type", "size_bytes", "chunk_count"}.issubset(artifact_fields)
    assert {"artifact_id", "chunk_index", "content"}.issubset(chunk_fields)


def test_artifact_store_splits_content_into_ordered_chunks():
    payload = b"a" * (ARTIFACT_CHUNK_SIZE_BYTES + 3)

    chunks = _split_chunks(payload)

    assert chunks == [b"a" * ARTIFACT_CHUNK_SIZE_BYTES, b"aaa"]


def test_legacy_artifact_blob_table_is_not_created():
    from pathlib import Path

    migrations = "\n".join(path.read_text(encoding="utf-8") for path in Path("migrations/models").glob("*.py"))

    assert "artifact_blobs" not in migrations


@pytest.mark.asyncio
async def test_write_blob_inserts_artifact_and_chunks_in_same_transaction():
    content = b"payload"
    content_hash = hashlib.sha256(content).hexdigest()
    artifact = _artifact(content_hash)
    conn = AsyncMock()
    conn.execute_query_dict.return_value = [{"id": artifact.id}]
    lookup = _mock_artifact_lookup(artifact)

    with (
        patch(
            "antcode_core.infrastructure.postgres.artifact_store.in_transaction",
            return_value=_TransactionContext(conn),
        ),
        patch.object(
            SourceArtifact,
            "filter",
            return_value=lookup,
        ),
        patch.object(SourceArtifactChunk, "bulk_create", new=AsyncMock()) as bulk_create,
    ):
        stored = await PostgresArtifactStore().write_blob(content)

    sql, _ = conn.execute_query_dict.await_args.args
    assert "ON CONFLICT (content_hash) DO NOTHING" in sql
    assert "RETURNING id" in sql
    assert stored.artifact_id == artifact.id
    chunks = bulk_create.await_args.args[0]
    assert [(chunk.artifact_id, chunk.chunk_index) for chunk in chunks] == [(42, 0)]
    assert bulk_create.await_args.kwargs["using_db"] is conn
    lookup.using_db.assert_called_once_with(conn)


@pytest.mark.asyncio
async def test_write_blob_reuses_concurrent_insert_without_duplicate_chunks():
    content = b"payload"
    content_hash = hashlib.sha256(content).hexdigest()
    artifact = _artifact(content_hash, artifact_id=73)
    conn = AsyncMock()
    conn.execute_query_dict.return_value = []

    with (
        patch(
            "antcode_core.infrastructure.postgres.artifact_store.in_transaction",
            return_value=_TransactionContext(conn),
        ),
        patch.object(
            SourceArtifact,
            "filter",
            return_value=_mock_artifact_lookup(artifact),
        ),
        patch.object(SourceArtifactChunk, "bulk_create", new=AsyncMock()) as bulk_create,
    ):
        stored = await PostgresArtifactStore().write_blob(content)

    assert stored.artifact_id == artifact.id
    bulk_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_blob_to_file_streams_ordered_chunks(tmp_path: Path):
    content = b"first-second"
    content_hash = hashlib.sha256(content).hexdigest()
    artifact = SimpleNamespace(
        id=42,
        content_hash=content_hash,
        size_bytes=len(content),
        media_type="application/octet-stream",
        chunk_count=2,
    )
    chunks = [SimpleNamespace(content=b"first-"), SimpleNamespace(content=b"second")]

    with (
        patch.object(SourceArtifact, "get_or_none", new=AsyncMock(return_value=artifact)),
        patch.object(SourceArtifactChunk, "get_or_none", new=AsyncMock(side_effect=chunks)) as get_chunk,
    ):
        destination = tmp_path / "artifact.bin"
        stored = await PostgresArtifactStore().read_blob_to_file(
            content_hash,
            destination,
            max_bytes=len(content),
        )

    assert destination.read_bytes() == content
    assert stored.content_hash == content_hash
    assert [call.kwargs["chunk_index"] for call in get_chunk.await_args_list] == [0, 1]


@pytest.mark.asyncio
async def test_read_blob_to_file_removes_partial_file_when_chunk_is_missing(tmp_path: Path):
    content_hash = hashlib.sha256(b"first-second").hexdigest()
    artifact = SimpleNamespace(
        id=42,
        content_hash=content_hash,
        size_bytes=12,
        media_type="application/octet-stream",
        chunk_count=2,
    )
    destination = tmp_path / "partial.bin"

    with (
        patch.object(SourceArtifact, "get_or_none", new=AsyncMock(return_value=artifact)),
        patch.object(
            SourceArtifactChunk,
            "get_or_none",
            new=AsyncMock(side_effect=[SimpleNamespace(content=b"first-"), None]),
        ),
    ):
        with pytest.raises(ValueError, match="chunk 缺失"):
            await PostgresArtifactStore().read_blob_to_file(content_hash, destination)

    assert not destination.exists()
