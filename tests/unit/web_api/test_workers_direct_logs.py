from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import data_pb2
from antcode_core.application.services.workers.log_ingest_fence import LogIngestFenceRejected
from antcode_core.common.log_batch_hash import deterministic_batch_id
from antcode_web_api.routes.v1 import workers_direct_logs as module
from fastapi import HTTPException

HTTP_BAD_REQUEST = 400
HTTP_FORBIDDEN = 403
HTTP_PRECONDITION_FAILED = 412


def _request(*, worker_id: str = "worker-1", lease_id: str = "lease-1") -> module.DirectLogBatchRequest:
    batch = data_pb2.LogBatch(
        worker_id=worker_id,
        lease_id=lease_id,
        entries=[data_pb2.LogEntry(run_id="run-1", content="line", sequence=1)],
    )
    batch.batch_id = deterministic_batch_id(batch.worker_id, batch.entries)
    payload = base64.b64encode(batch.SerializeToString()).decode("ascii")
    return module.DirectLogBatchRequest(operation="logs", payload_base64=payload)


@pytest.mark.asyncio
async def test_direct_log_batch_uses_database_and_atomic_redis_fences(monkeypatch) -> None:
    redis = object()
    owns = AsyncMock()
    append = AsyncMock(return_value="20-0")
    monkeypatch.setattr(module, "require_worker_owns_runs_for_lease", owns)
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(module, "append_fenced_log_batch", append)
    worker = SimpleNamespace(public_id="worker-1")

    response = await module.ingest_direct_log_batch(worker, _request())

    assert response.data == {"written": True, "message_id": "20-0"}
    owns.assert_awaited_once_with(worker, {"run-1"}, lease_id="lease-1")
    append.assert_awaited_once()
    assert append.await_args.args[0] is redis
    assert append.await_args.kwargs["run_ids"] == {"run-1"}


@pytest.mark.asyncio
async def test_direct_log_batch_rejects_worker_identity_mismatch() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await module.ingest_direct_log_batch(SimpleNamespace(public_id="worker-1"), _request(worker_id="worker-2"))

    assert exc_info.value.status_code == HTTP_FORBIDDEN


@pytest.mark.asyncio
async def test_direct_log_batch_maps_stale_generation_to_precondition(monkeypatch) -> None:
    monkeypatch.setattr(module, "require_worker_owns_runs_for_lease", AsyncMock())
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        module,
        "append_fenced_log_batch",
        AsyncMock(side_effect=LogIngestFenceRejected("lease_stale")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await module.ingest_direct_log_batch(SimpleNamespace(public_id="worker-1"), _request())

    assert exc_info.value.status_code == HTTP_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_direct_log_batch_rejects_invalid_base64() -> None:
    request = module.DirectLogBatchRequest(operation="logs", payload_base64="not-base64")

    with pytest.raises(HTTPException) as exc_info:
        await module.ingest_direct_log_batch(SimpleNamespace(public_id="worker-1"), request)

    assert exc_info.value.status_code == HTTP_BAD_REQUEST
