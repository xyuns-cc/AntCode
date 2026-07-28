"""Spider data transport and service acknowledgement semantics."""

from __future__ import annotations

from typing import Any

import grpc
import pytest
from antcode_contracts import data_pb2
from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.services.data_service import GatewayDataService
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


class RejectingSpiderStub:
    async def StreamSpiderData(self, requests, metadata):
        del metadata
        async for _batch in requests:
            pass
        return data_pb2.SpiderDataAck(accepted=0, failed=0)


class FailingSpiderHandler:
    async def handle_batch(self, batch: data_pb2.SpiderDataBatch) -> tuple[int, int]:
        raise RuntimeError(f"redis failed for {batch.run_id}")


class AbortCalled(RuntimeError):
    pass


class AbortContext:
    def __init__(self) -> None:
        self.abort_calls: list[tuple[grpc.StatusCode, str]] = []

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.abort_calls.append((code, details))
        raise AbortCalled(details)

    def auth_context(self) -> dict:
        return {}


async def _single(message):
    yield message


async def _allow_ownership(worker_id: str, run_ids: set[str]) -> None:
    assert worker_id == "worker-1"
    assert run_ids == {"run-1"}


async def _allow_spider_ownership(
    worker_id: str,
    run_id: str,
    project_id: str,
    *,
    lease_id: str,
) -> None:
    assert (worker_id, run_id, project_id, lease_id) == ("worker-1", "run-1", "project-1", "lease-1")


async def _allow_lease(worker_id: str, lease_id: str) -> bool:
    return (worker_id, lease_id) == ("worker-1", "lease-1")


def _item() -> dict[str, Any]:
    return {
        "item_id": "item-1",
        "project_id": "project-1",
        "spider_name": "spider",
        "item_type": "default",
        "data": "{}",
        "timestamp": "2026-07-13T00:00:00",
        "sequence": 1,
    }


@pytest.mark.asyncio
async def test_gateway_transport_requires_exact_item_ack_count() -> None:
    transport = GatewayTransport(
        gateway_config=GatewayConfig(worker_id="worker-1"),
    )
    transport._running = True
    transport._data_stub = RejectingSpiderStub()

    assert await transport.report_spider_data("run-1", [_item()]) is False


@pytest.mark.asyncio
async def test_gateway_service_aborts_meta_stream_when_persistence_fails() -> None:
    context = AbortContext()
    service = GatewayDataService(
        spider_data_handler=FailingSpiderHandler(),
        ownership_verifier=_allow_ownership,
        spider_ownership_verifier=_allow_spider_ownership,
        lease_verifier=_allow_lease,
    )
    original = grpc.stream_unary_rpc_method_handler(service.StreamSpiderData)
    wrapped = AuthInterceptor()._make_mtls_wrapped_handler(original, "worker-1")
    batch = data_pb2.SpiderDataBatch(
        worker_id="worker-1",
        run_id="run-1",
        project_id="project-1",
        lease_id="lease-1",
        meta=data_pb2.SpiderMetaUpdate(status="completed", items_count=0),
    )

    with pytest.raises(AbortCalled, match="redis failed"):
        await wrapped.stream_unary(_single(batch), context)
    assert context.abort_calls[-1][0] is grpc.StatusCode.UNAVAILABLE
