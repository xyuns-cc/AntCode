"""Wire validation and temporary-file helpers for artifact RPCs."""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path, PurePosixPath

import aiofiles  # type: ignore[import-untyped]
import grpc
from antcode_contracts import artifact_pb2

MEBIBYTE = 1024 * 1024
ARTIFACT_CHUNK_BYTES = MEBIBYTE
MAX_ARTIFACT_BYTES = 100 * MEBIBYTE
MAX_ARTIFACT_NAME_LENGTH = 500
MAX_MEDIA_TYPE_LENGTH = 128
MAX_IDENTIFIER_LENGTH = 128
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactTooLargeError(ValueError):
    pass


async def read_upload_metadata(request_iterator, context) -> artifact_pb2.TaskArtifactMetadata:
    try:
        first = await anext(request_iterator)
    except StopAsyncIteration:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "artifact upload 缺少 metadata")
    if first.WhichOneof("payload") != "metadata":
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "artifact upload 首帧必须是 metadata")
    return first.metadata


async def receive_upload(request_iterator, context, path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    received = 0
    async with aiofiles.open(path, "xb") as output:
        async for frame in request_iterator:
            if frame.WhichOneof("payload") != "chunk":
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "artifact upload metadata 只能出现一次")
            data = bytes(frame.chunk.data)
            if not data or len(data) > ARTIFACT_CHUNK_BYTES:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "artifact chunk 大小无效")
            if frame.chunk.offset != received:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "artifact chunk offset 不连续")
            received += len(data)
            if received > MAX_ARTIFACT_BYTES:
                await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "artifact 超过 100 MiB 上限")
            digest.update(data)
            await output.write(data)
    return received, digest.hexdigest()


async def validate_download_request(request, context) -> str:
    try:
        digest = _require_sha256(request.sha256)
        _require_identifier(request.worker_id, "worker_id")
        _require_identifier(request.lease_id, "lease_id")
        _require_identifier(request.run_id, "run_id")
        _require_identifier(request.project_id, "project_id")
        _require_size(request.size_bytes)
        return digest
    except ArtifactTooLargeError as exc:
        await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc))
    except ValueError as exc:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
    raise AssertionError("context.abort returned")


async def validate_upload_metadata(metadata, context) -> str:
    try:
        digest = _require_sha256(metadata.sha256)
        _require_identifier(metadata.worker_id, "worker_id")
        _require_identifier(metadata.lease_id, "lease_id")
        _require_identifier(metadata.run_id, "run_id")
        _require_size(metadata.size_bytes)
        _require_artifact_name(metadata.name)
        _require_media_type(metadata.media_type)
        return digest
    except ArtifactTooLargeError as exc:
        await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc))
    except ValueError as exc:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
    raise AssertionError("context.abort returned")


def require_upload_evidence(metadata, actual_size: int, actual_hash: str) -> None:
    if actual_size != metadata.size_bytes:
        raise ValueError("artifact 实际大小与声明不一致")
    if actual_hash != metadata.sha256:
        raise ValueError("artifact 实际 sha256 与声明不一致")


def temporary_path() -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="antcode-artifact-", delete=False)
    path = Path(handle.name)
    handle.close()
    path.unlink()
    return path


def _require_sha256(value: str) -> str:
    if not SHA256_RE.fullmatch(value or ""):
        raise ValueError("sha256 必须是 64 位小写十六进制")
    return value


def _require_size(value: int) -> None:
    if value < 0:
        raise ValueError("artifact size_bytes 不能为负数")
    if value > MAX_ARTIFACT_BYTES:
        raise ArtifactTooLargeError("artifact 超过 100 MiB 上限")


def _require_identifier(value: str, name: str) -> None:
    if not value or value != value.strip() or len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{name} 必须非空且无首尾空白")


def _require_artifact_name(value: str) -> None:
    path = PurePosixPath(value)
    if not value or len(value) > MAX_ARTIFACT_NAME_LENGTH or path.is_absolute():
        raise ValueError("artifact name 无效")
    if "\\" in value or "\x00" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact name 无效")


def _require_media_type(value: str) -> None:
    invalid = not value or len(value) > MAX_MEDIA_TYPE_LENGTH
    if invalid or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("artifact media_type 无效")


__all__ = [
    "ARTIFACT_CHUNK_BYTES",
    "MAX_ARTIFACT_BYTES",
    "read_upload_metadata",
    "receive_upload",
    "require_upload_evidence",
    "temporary_path",
    "validate_download_request",
    "validate_upload_metadata",
]
