from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, call

import grpc
import pytest
from antcode_contracts import artifact_pb2, artifact_pb2_grpc
from antcode_gateway.grpc_rejection import make_rate_limit_rejection
from antcode_gateway.rate_limit import RateLimitResult
from antcode_gateway.services.artifact_transfer import ARTIFACT_CHUNK_BYTES, MAX_ARTIFACT_BYTES

from tests.unit.gateway.artifact_service_helpers import (
    FakeArtifactStore as _ArtifactStore,
)
from tests.unit.gateway.artifact_service_helpers import (
    make_context as _context,
)
from tests.unit.gateway.artifact_service_helpers import (
    make_download_request as _download_request,
)
from tests.unit.gateway.artifact_service_helpers import (
    make_service as _service,
)
from tests.unit.gateway.artifact_service_helpers import (
    make_upload_frames as _upload_frames,
)
from tests.unit.gateway.artifact_service_helpers import (
    run_download as _download,
)
from tests.unit.gateway.artifact_service_helpers import (
    run_upload as _upload,
)


def test_artifact_proto_streaming_cardinality():
    service = artifact_pb2.DESCRIPTOR.services_by_name["ArtifactService"]

    download = service.methods_by_name["DownloadSourceBundle"]
    upload = service.methods_by_name["UploadTaskArtifact"]

    assert download.client_streaming is False
    assert download.server_streaming is True
    assert upload.client_streaming is True
    assert upload.server_streaming is False
    assert hasattr(artifact_pb2_grpc, "ArtifactServiceStub")


@pytest.mark.asyncio
async def test_download_source_bundle_is_authorized_fenced_and_chunked():
    content = b"a" * ARTIFACT_CHUNK_BYTES + b"tail"
    store = _ArtifactStore(content)
    service = _service(store)

    chunks = await _download(service, _download_request(content), _context())

    assert [chunk.offset for chunk in chunks] == [0, ARTIFACT_CHUNK_BYTES]
    assert b"".join(chunk.data for chunk in chunks) == content
    service._ownership_verifier.assert_has_awaits([call("worker-a", "run-a", "lease-a")] * 2)
    service._source_authorizer.assert_awaited_once_with(
        "run-a",
        "project-a",
        hashlib.sha256(content).hexdigest(),
    )


@pytest.mark.asyncio
async def test_download_rejects_stale_lease_before_storage_read():
    store = _ArtifactStore(b"bundle")
    service = _service(store, lease_current=False)
    context = _context()

    with pytest.raises(grpc.aio.AbortError):
        await _download(service, _download_request(store.content), context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.FAILED_PRECONDITION
    assert store.read_hashes == []


@pytest.mark.asyncio
async def test_download_rejects_source_snapshot_mismatch():
    store = _ArtifactStore(b"bundle")
    service = _service(store)
    service._source_authorizer.side_effect = PermissionError("foreign hash")
    context = _context()

    with pytest.raises(grpc.aio.AbortError):
        await _download(service, _download_request(store.content), context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.PERMISSION_DENIED
    assert store.read_hashes == []


@pytest.mark.asyncio
async def test_upload_task_artifact_validates_chunks_and_persists():
    content = b"a" * ARTIFACT_CHUNK_BYTES + b"tail"
    store = _ArtifactStore()
    service = _service(store)

    response = await _upload(service, _upload_frames(content), _context())

    assert response.sha256 == hashlib.sha256(content).hexdigest()
    assert response.size_bytes == len(content)
    assert store.writes == [(content, "application/json")]
    service._ownership_verifier.assert_has_awaits([call("worker-a", "run-a", "lease-a")] * 2)


@pytest.mark.asyncio
async def test_upload_rejects_terminal_task_run():
    store = _ArtifactStore()
    ownership = AsyncMock(side_effect=PermissionError("TaskRun 已进入终态"))
    context = _context()

    with pytest.raises(grpc.aio.AbortError):
        await _upload(
            _service(store, ownership_verifier=ownership),
            _upload_frames(b"payload"),
            context,
        )

    assert context.abort.await_args.args[0] == grpc.StatusCode.PERMISSION_DENIED
    assert store.writes == []


@pytest.mark.asyncio
async def test_upload_rejects_task_run_from_old_lease_before_storage_access():
    store = _ArtifactStore()
    ownership = AsyncMock(side_effect=PermissionError("TaskRun lease_id 与当前 Worker 代际不匹配"))
    context = _context()

    with pytest.raises(grpc.aio.AbortError):
        await _upload(
            _service(store, ownership_verifier=ownership),
            _upload_frames(b"payload"),
            context,
        )

    assert context.abort.await_args.args[0] == grpc.StatusCode.PERMISSION_DENIED
    assert store.read_hashes == []
    assert store.writes == []


@pytest.mark.asyncio
async def test_upload_rejects_non_contiguous_chunk_offset():
    content = b"payload"
    frames = _upload_frames(content)
    frames[1].chunk.offset = 1
    store = _ArtifactStore()
    context = _context()

    with pytest.raises(grpc.aio.AbortError):
        await _upload(_service(store), frames, context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.INVALID_ARGUMENT
    assert store.writes == []


@pytest.mark.asyncio
async def test_upload_rejects_hash_mismatch():
    content = b"payload"
    frames = _upload_frames(content, sha256="a" * 64)
    store = _ArtifactStore()
    context = _context()

    with pytest.raises(grpc.aio.AbortError):
        await _upload(_service(store), frames, context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.INVALID_ARGUMENT
    assert store.writes == []


@pytest.mark.asyncio
async def test_upload_rejects_declared_size_over_100_mib():
    frames = _upload_frames(b"", size_bytes=MAX_ARTIFACT_BYTES + 1)
    store = _ArtifactStore()
    context = _context()

    with pytest.raises(grpc.aio.AbortError):
        await _upload(_service(store), frames, context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert store.writes == []


@pytest.mark.parametrize(
    ("original", "attribute"),
    [
        (grpc.unary_stream_rpc_method_handler(AsyncMock()), "unary_stream"),
        (grpc.stream_unary_rpc_method_handler(AsyncMock()), "stream_unary"),
    ],
)
def test_rate_limit_rejection_preserves_streaming_cardinality(original, attribute):
    result = RateLimitResult(allowed=False, retry_after=1.0, reset_at=2.0)

    rejected = make_rate_limit_rejection(original, result)

    assert getattr(rejected, attribute) is not None
