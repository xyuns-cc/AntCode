"""Worker artifact transfer adapter tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import artifact_pb2
from antcode_worker.app.wiring import _create_artifact_transfer_store
from antcode_worker.artifact_transfer import (
    PostgresArtifactTransferStore,
    SourceBundleDownload,
    TaskArtifactUpload,
)
from antcode_worker.transport.gateway.artifacts import GatewayArtifactTransferStore


class _DownloadStream:
    def __init__(self, chunks: list[object]):
        self._chunks = chunks

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


class _ArtifactStub:
    def __init__(self, download: bytes):
        midpoint = len(download) // 2
        self.download_request = None
        self.upload_frames: list[object] = []
        self._download = download
        self._midpoint = midpoint

    def DownloadSourceBundle(self, request, **kwargs):
        self.download_request = (request, kwargs)
        return _DownloadStream(
            [
                artifact_pb2.ArtifactChunk(offset=0, data=self._download[: self._midpoint]),
                artifact_pb2.ArtifactChunk(
                    offset=self._midpoint,
                    data=self._download[self._midpoint :],
                ),
            ]
        )

    async def UploadTaskArtifact(self, frames, **kwargs):
        self.upload_kwargs = kwargs
        self.upload_frames = [frame async for frame in frames]
        metadata = self.upload_frames[0].metadata
        return artifact_pb2.ArtifactUploadResponse(
            uri=f"pgartifact://{metadata.sha256}",
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            media_type=metadata.media_type,
        )


def _gateway_store(stub: object) -> GatewayArtifactTransferStore:
    transport = SimpleNamespace(
        gateway_config=SimpleNamespace(artifact_transfer_timeout=300.0),
        artifact_rpc_session=lambda: (
            stub,
            "worker-1",
            "lease-1",
            (("x-api-key", "key"),),
        ),
    )
    return GatewayArtifactTransferStore(transport)


@pytest.mark.asyncio
async def test_gateway_store_downloads_verified_chunks(tmp_path: Path) -> None:
    content = b"gateway-source-bundle"
    digest = hashlib.sha256(content).hexdigest()
    stub = _ArtifactStub(content)
    destination = tmp_path / "source.bundle"

    stored = await _gateway_store(stub).download_source_bundle(
        SourceBundleDownload("run-1", "project-1", digest, len(content), 1024),
        destination,
    )

    request, kwargs = stub.download_request
    assert (request.worker_id, request.lease_id, request.run_id) == (
        "worker-1",
        "lease-1",
        "run-1",
    )
    assert kwargs["timeout"] == 300.0
    assert destination.read_bytes() == content
    assert stored.content_hash == digest


@pytest.mark.asyncio
async def test_gateway_store_uploads_metadata_before_chunks() -> None:
    content = b"artifact-output"
    digest = hashlib.sha256(content).hexdigest()
    stub = _ArtifactStub(b"")

    stored = await _gateway_store(stub).upload_task_artifact(
        TaskArtifactUpload("run-1", "result.txt", "text/plain", content, digest)
    )

    assert stub.upload_frames[0].WhichOneof("payload") == "metadata"
    metadata = stub.upload_frames[0].metadata
    assert (metadata.worker_id, metadata.lease_id, metadata.name) == (
        "worker-1",
        "lease-1",
        "result.txt",
    )
    assert b"".join(frame.chunk.data for frame in stub.upload_frames[1:]) == content
    assert stored.uri == f"pgartifact://{digest}"


@pytest.mark.asyncio
async def test_direct_store_wraps_postgres_contract(tmp_path: Path) -> None:
    content = b"direct"
    digest = hashlib.sha256(content).hexdigest()
    raw_store = SimpleNamespace(
        read_blob_to_file=AsyncMock(
            return_value=SimpleNamespace(
                uri=f"pgartifact://{digest}",
                content_hash=digest,
                size_bytes=len(content),
                media_type="application/octet-stream",
            )
        ),
        write_blob=AsyncMock(
            return_value=SimpleNamespace(
                uri=f"pgartifact://{digest}",
                content_hash=digest,
                size_bytes=len(content),
                media_type="text/plain",
            )
        ),
    )
    store = PostgresArtifactTransferStore(raw_store)
    await store.download_source_bundle(
        SourceBundleDownload("run-1", "project-1", digest, len(content), 1024),
        tmp_path / "source.bundle",
    )
    await store.upload_task_artifact(TaskArtifactUpload("run-1", "result.txt", "text/plain", content, digest))

    raw_store.read_blob_to_file.assert_awaited_once()
    raw_store.write_blob.assert_awaited_once_with(content, media_type="text/plain")


def test_wiring_selects_artifact_store_by_transport_mode() -> None:
    transport = object()

    direct = _create_artifact_transfer_store(
        SimpleNamespace(transport_mode="direct"),
        transport,
    )
    gateway = _create_artifact_transfer_store(
        SimpleNamespace(transport_mode="gateway"),
        transport,
    )

    assert isinstance(direct, PostgresArtifactTransferStore)
    assert isinstance(gateway, GatewayArtifactTransferStore)
    assert gateway._transport is transport


def test_gateway_wiring_does_not_construct_postgres_store(monkeypatch) -> None:
    from antcode_core.infrastructure.postgres import artifact_store as postgres_module

    def fail_if_constructed():
        raise AssertionError("Gateway wiring must not construct PostgresArtifactStore")

    monkeypatch.setattr(postgres_module, "PostgresArtifactStore", fail_if_constructed)

    store = _create_artifact_transfer_store(
        SimpleNamespace(transport_mode="gateway"),
        object(),
    )

    assert isinstance(store, GatewayArtifactTransferStore)
