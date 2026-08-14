from __future__ import annotations

import base64
import json

import httpx
import pytest
from antcode_core.common.security import (
    WORKER_HTTP_SIGNATURE_HEADER,
    WORKER_HTTP_SIGNATURE_VERSION,
    generate_hmac_signature,
)
from antcode_worker.transport.base import GenerationLostError
from antcode_worker.transport.redis.direct_control import DirectControlClient, DirectLeaseGrant


def _client(handler) -> tuple[DirectControlClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DirectControlClient(
        api_base_url="https://control.example.com",
        worker_id="worker-1",
        api_key="api-key",
        secret_key="secret-key",
        client=http,
    )
    return client, http


def test_remote_plain_http_control_plane_is_rejected() -> None:
    with pytest.raises(ValueError, match="必须使用 HTTPS"):
        DirectControlClient(
            api_base_url="http://control.example.com",
            worker_id="worker-1",
            api_key="api-key",
            secret_key="secret-key",
        )


def test_internal_plain_http_requires_explicit_opt_in() -> None:
    client = DirectControlClient(
        api_base_url="http://web-api:8000",
        worker_id="worker-1",
        api_key="api-key",
        secret_key="secret-key",
        allow_insecure_internal=True,
    )
    assert client._base_url == "http://web-api:8000"


@pytest.mark.asyncio
async def test_lease_request_signs_the_exact_operation_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload == {
            "operation": "lease",
            "current_lease_id": "lease-1",
            "metrics": {"cpu": 10.0},
            "capabilities": {"task_types": '["code"]'},
        }
        timestamp = int(request.headers["X-Timestamp"])
        nonce = request.headers["X-Nonce"]
        expected = generate_hmac_signature(
            request.content,
            "secret-key",
            method=request.method,
            path=request.url.path,
            timestamp=timestamp,
            nonce=nonce,
        )["X-Signature"]
        assert request.headers["X-Signature"] == expected
        assert request.headers[WORKER_HTTP_SIGNATURE_HEADER] == WORKER_HTTP_SIGNATURE_VERSION
        assert request.headers["Authorization"] == "Bearer api-key"
        assert request.headers["Content-Type"] == "application/json"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "lease_id": "lease-1",
                    "expires_at_ms": 100,
                    "renew_after_ms": 10,
                    "ttl_ms": 30,
                    "revoked": False,
                },
            },
        )

    client, http = _client(handler)
    try:
        result = await client.lease_renew("lease-1", {"cpu": 10.0}, {"task_types": '["code"]'})
    finally:
        await http.aclose()
    assert result == DirectLeaseGrant(
        lease_id="lease-1",
        expires_at_ms=100,
        renew_after_ms=10,
        ttl_ms=30,
        revoked=False,
    )


@pytest.mark.asyncio
async def test_ownership_operations_include_non_replayable_discriminator() -> None:
    observed: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed.append((request.url.path.rsplit("/", 1)[-1], payload))
        field = {"claim": "acquired", "renew": "renewed", "release": "released"}[payload["operation"]]
        return httpx.Response(200, json={"success": True, "data": {field: True}})

    client, http = _client(handler)
    try:
        assert await client.claim_run_ownership("lease-1", "run-1", 1000)
        assert await client.renew_run_ownership("lease-1", "run-1", 1000)
        assert await client.release_run_ownership("lease-1", "run-1")
    finally:
        await http.aclose()
    assert [payload["operation"] for _path, payload in observed] == ["claim", "renew", "release"]
    assert "ttl_ms" not in observed[-1][1]


@pytest.mark.asyncio
async def test_http_failure_is_explicit() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"detail": "lease generation superseded"})

    client, http = _client(handler)
    try:
        with pytest.raises(GenerationLostError, match="generation"):
            await client.claim_run_ownership("lease-1", "run-1", 1000)
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_spider_writes_use_authenticated_control_plane_operations() -> None:
    observed: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed.append((request.url.path.rsplit("/", 1)[-1], payload))
        return httpx.Response(200, json={"success": True, "data": {"written": True}})

    client, http = _client(handler)
    try:
        assert await client.report_spider_items(
            lease_id="lease-1",
            run_id="run-1",
            project_id="project-1",
            items=[{"item_id": "item-1"}],
        )
        assert await client.update_spider_meta(
            lease_id="lease-1",
            run_id="run-1",
            project_id="project-1",
            meta={"status": "running"},
        )
    finally:
        await http.aclose()

    assert [operation for operation, _payload in observed] == ["spider-items", "spider-meta"]
    assert observed[0][1]["lease_id"] == "lease-1"
    assert observed[1][1]["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_log_batches_use_authenticated_control_plane_operation() -> None:
    raw_payload = b"log-batch-protobuf"

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path.endswith("/direct-control/logs")
        assert payload["operation"] == "logs"
        assert base64.b64decode(payload["payload_base64"], validate=True) == raw_payload
        timestamp = int(request.headers["X-Timestamp"])
        nonce = request.headers["X-Nonce"]
        expected = generate_hmac_signature(
            request.content,
            "secret-key",
            method=request.method,
            path=request.url.path,
            timestamp=timestamp,
            nonce=nonce,
        )["X-Signature"]
        assert request.headers["X-Signature"] == expected
        return httpx.Response(200, json={"success": True, "data": {"written": True}})

    client, http = _client(handler)
    try:
        assert await client.report_log_batch(raw_payload)
    finally:
        await http.aclose()
