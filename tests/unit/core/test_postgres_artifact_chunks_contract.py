import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.domain.models import SourceArtifact, SourceArtifactChunk
from antcode_core.infrastructure.postgres import artifact_store as module
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


async def _chunks(values):
    for value in values:
        yield value


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
        patch.object(module, "_iter_chunk_contents", return_value=_chunks([b"first-", b"second"])),
    ):
        destination = tmp_path / "artifact.bin"
        stored = await PostgresArtifactStore().read_blob_to_file(
            content_hash,
            destination,
            max_bytes=len(content),
        )

    assert destination.read_bytes() == content
    assert stored.content_hash == content_hash


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
        patch.object(module, "_iter_chunk_contents", return_value=_chunks([b"first-"])),
    ):
        with pytest.raises(ValueError, match="大小不一致"):
            await PostgresArtifactStore().read_blob_to_file(content_hash, destination)

    assert not destination.exists()


@pytest.mark.asyncio
async def test_iter_blob_yields_chunks_without_joining_content():
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
        patch.object(module, "_iter_chunk_contents", return_value=_chunks([b"first-", b"second"])),
    ):
        streamed = [chunk async for chunk in PostgresArtifactStore().iter_blob(content_hash)]

    assert streamed == [b"first-", b"second"]


@pytest.mark.asyncio
async def test_chunk_reader_uses_one_server_side_cursor():
    class _Cursor:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not rows:
                raise StopAsyncIteration
            return rows.pop(0)

    rows = [
        {"chunk_index": 0, "content": b"first-"},
        {"chunk_index": 1, "content": b"second"},
    ]
    raw_connection = MagicMock()
    raw_connection.cursor.return_value = _Cursor()
    transaction = SimpleNamespace(_connection=raw_connection)

    with patch.object(module, "in_transaction", return_value=_TransactionContext(transaction)):
        chunks = [chunk async for chunk in module._iter_chunk_contents(42, 2)]

    assert chunks == [b"first-", b"second"]
    raw_connection.cursor.assert_called_once()


@pytest.mark.asyncio
async def test_stat_blob_rejects_incomplete_chunk_set():
    content_hash = hashlib.sha256(b"payload").hexdigest()
    artifact = _artifact(content_hash)
    chunk_query = MagicMock()
    chunk_query.count = AsyncMock(return_value=0)

    with (
        patch.object(SourceArtifact, "get_or_none", new=AsyncMock(return_value=artifact)),
        patch.object(SourceArtifactChunk, "filter", return_value=chunk_query),
    ):
        with pytest.raises(ValueError, match="chunk 数量不一致"):
            await PostgresArtifactStore().stat_blob(content_hash)
