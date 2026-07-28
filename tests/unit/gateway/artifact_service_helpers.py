"""Gateway artifact service 单测共享脚手架。

供 ``test_artifact_service.py`` 与 ``test_artifact_run_quota.py`` 复用
（无 ``test_`` 前缀，不会被 pytest 收集）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
from antcode_contracts import artifact_pb2
from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.services.artifact_service import GatewayArtifactService
from antcode_gateway.services.artifact_transfer import ARTIFACT_CHUNK_BYTES


class FakeArtifactStore:
    def __init__(self, content: bytes = b"") -> None:
        self.content = content
        self.read_hashes: list[str] = []
        self.writes: list[tuple[bytes, str]] = []

    async def read_blob_to_file(self, content_hash, destination: Path, *, max_bytes: int):
        self.read_hashes.append(content_hash)
        assert len(self.content) <= max_bytes
        destination.write_bytes(self.content)
        return self._stored(self.content, "application/vnd.antcode.source-bundle")

    async def write_blob(self, content: bytes, media_type: str, metadata=None):
        del metadata
        self.writes.append((content, media_type))
        return self._stored(content, media_type)

    async def write_blob_from_file(self, source: Path, media_type: str, metadata=None):
        # P2 §4.2: 上传路径改为流式文件写入，不再整读进内存。
        del metadata
        content = source.read_bytes()
        self.writes.append((content, media_type))
        return self._stored(content, media_type)

    @staticmethod
    def _stored(content: bytes, media_type: str):
        digest = hashlib.sha256(content).hexdigest()
        return SimpleNamespace(
            uri=f"pgartifact://{digest}",
            content_hash=digest,
            size_bytes=len(content),
            media_type=media_type,
        )


def make_context() -> MagicMock:
    context = MagicMock()
    context.auth_context.return_value = {}
    context.abort = AsyncMock(side_effect=grpc.aio.AbortError())
    return context


def make_service(store: FakeArtifactStore, *, lease_current: bool = True, ownership_verifier=None, run_quota=None):
    return GatewayArtifactService(
        artifact_store=store,
        lease_verifier=AsyncMock(return_value=lease_current),
        ownership_verifier=ownership_verifier or AsyncMock(),
        source_authorizer=AsyncMock(),
        run_quota=run_quota,
    )


async def run_download(service, request, context):
    original = grpc.unary_stream_rpc_method_handler(service.DownloadSourceBundle)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    return [chunk async for chunk in wrapped.unary_stream(request, context)]


async def run_upload(service, frames, context):
    async def requests():
        for frame in frames:
            yield frame

    original = grpc.stream_unary_rpc_method_handler(service.UploadTaskArtifact)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-a")
    return await wrapped.stream_unary(requests(), context)


def make_download_request(content: bytes, **overrides):
    values = {
        "worker_id": "worker-a",
        "lease_id": "lease-a",
        "run_id": "run-a",
        "project_id": "project-a",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }
    values.update(overrides)
    return artifact_pb2.SourceBundleDownloadRequest(**values)


def make_upload_frames(content: bytes, **overrides):
    values = {
        "worker_id": "worker-a",
        "lease_id": "lease-a",
        "run_id": "run-a",
        "name": "reports/result.json",
        "media_type": "application/json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }
    values.update(overrides)
    metadata = artifact_pb2.ArtifactUploadFrame(metadata=artifact_pb2.TaskArtifactMetadata(**values))
    chunks = []
    for offset in range(0, len(content), ARTIFACT_CHUNK_BYTES):
        chunks.append(
            artifact_pb2.ArtifactUploadFrame(
                chunk=artifact_pb2.ArtifactChunk(
                    offset=offset,
                    data=content[offset : offset + ARTIFACT_CHUNK_BYTES],
                )
            )
        )
    return [metadata, *chunks]
