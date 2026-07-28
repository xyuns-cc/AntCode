"""ArtifactService client adapter for backendless Gateway Workers."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiofiles  # type: ignore[import-untyped]

from antcode_worker.artifact_transfer import (
    SourceBundleDownload,
    StoredArtifact,
    TaskArtifactUpload,
)

ARTIFACT_CHUNK_BYTES = 1024 * 1024


class GatewayArtifactTransferStore:
    """Stream artifacts through the authenticated Gateway transport channel."""

    def __init__(self, transport: Any):
        self._transport = transport

    async def download_source_bundle(
        self,
        request: SourceBundleDownload,
        destination: Path,
    ) -> StoredArtifact:
        from antcode_contracts import artifact_pb2

        stub, worker_id, lease_id, auth_metadata = self._require_session()
        rpc_request = artifact_pb2.SourceBundleDownloadRequest(
            worker_id=worker_id,
            lease_id=lease_id,
            run_id=request.run_id,
            project_id=request.project_id,
            sha256=request.content_hash,
            size_bytes=request.size_bytes,
        )
        stream = stub.DownloadSourceBundle(
            rpc_request,
            metadata=auth_metadata,
            timeout=self._transfer_timeout(),
        )
        try:
            return await self._write_download(stream, request, destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            cancel = getattr(stream, "cancel", None)
            if callable(cancel):
                cancel()
            raise

    async def upload_task_artifact(
        self,
        request: TaskArtifactUpload,
    ) -> StoredArtifact:
        stub, worker_id, lease_id, auth_metadata = self._require_session()
        response = await stub.UploadTaskArtifact(
            self._upload_frames(request, worker_id, lease_id),
            metadata=auth_metadata,
            timeout=self._transfer_timeout(),
        )
        self._validate_upload_response(response, request)
        return StoredArtifact(
            uri=response.uri,
            content_hash=response.sha256,
            size_bytes=int(response.size_bytes),
            media_type=response.media_type,
        )

    async def _write_download(
        self,
        stream: Any,
        request: SourceBundleDownload,
        destination: Path,
    ) -> StoredArtifact:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        async with aiofiles.open(destination, "wb") as output:
            async for chunk in stream:
                data = bytes(chunk.data)
                if int(chunk.offset) != written:
                    raise ValueError("Gateway artifact chunk offset 不连续")
                written += len(data)
                if written > request.max_bytes:
                    raise ValueError("Gateway source bundle 超过下载上限")
                digest.update(data)
                await output.write(data)
        if written != request.size_bytes:
            raise ValueError("Gateway source bundle 大小不一致")
        if digest.hexdigest() != request.content_hash:
            raise ValueError("Gateway source bundle sha256 不一致")
        return StoredArtifact(
            uri=f"pgartifact://{request.content_hash}",
            content_hash=request.content_hash,
            size_bytes=written,
            media_type="application/octet-stream",
        )

    async def _upload_frames(
        self,
        request: TaskArtifactUpload,
        worker_id: str,
        lease_id: str,
    ) -> AsyncIterator[Any]:
        from antcode_contracts import artifact_pb2

        actual_hash = hashlib.sha256(request.content).hexdigest()
        if actual_hash != request.content_hash:
            raise ValueError("task artifact sha256 与内容不一致")
        metadata = artifact_pb2.TaskArtifactMetadata(
            worker_id=worker_id,
            lease_id=lease_id,
            run_id=request.run_id,
            name=request.name,
            media_type=request.media_type,
            sha256=request.content_hash,
            size_bytes=request.size_bytes,
        )
        yield artifact_pb2.ArtifactUploadFrame(metadata=metadata)
        for offset in range(0, request.size_bytes, ARTIFACT_CHUNK_BYTES):
            chunk = artifact_pb2.ArtifactChunk(
                offset=offset,
                data=request.content[offset : offset + ARTIFACT_CHUNK_BYTES],
            )
            yield artifact_pb2.ArtifactUploadFrame(chunk=chunk)

    def _require_session(self) -> tuple[Any, str, str, tuple[tuple[str, str], ...]]:
        return self._transport.artifact_rpc_session()

    def _transfer_timeout(self) -> float:
        return float(self._transport.gateway_config.artifact_transfer_timeout)

    @staticmethod
    def _validate_upload_response(response: Any, request: TaskArtifactUpload) -> None:
        expected_uri = f"pgartifact://{request.content_hash}"
        if response.uri != expected_uri or response.sha256 != request.content_hash:
            raise ValueError("Gateway artifact upload 返回的内容标识不一致")
        if int(response.size_bytes) != request.size_bytes:
            raise ValueError("Gateway artifact upload 返回的大小不一致")
        if response.media_type != request.media_type:
            raise ValueError("Gateway artifact upload 返回的 media_type 不一致")


__all__ = ["ARTIFACT_CHUNK_BYTES", "GatewayArtifactTransferStore"]
