import json
import time

import pytest
from antcode_core.common.security import generate_hmac_signature
from antcode_core.common.security.worker_auth import (
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    WorkerAuthVerifier,
)
from fastapi import HTTPException
from starlette.requests import Request

HTTP_UNAUTHORIZED = 401


class _RateLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, int, int]] = []

    async def is_allowed(self, identifier: str, limit: int, period: int) -> bool:
        self.calls.append((identifier, limit, period))
        return self.allowed


def _request(headers: dict[str, str], payload: dict) -> Request:
    body = json.dumps(payload).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(name.lower().encode(), value.encode()) for name, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw_headers}, receive)


@pytest.mark.asyncio
async def test_signature_uses_shared_secret_and_nonce_services() -> None:
    claimed: list[tuple[str, str]] = []

    async def load_secret(worker_id: str) -> str | None:
        return "shared-secret" if worker_id == "worker-1" else None

    async def claim_nonce(worker_id: str, nonce: str) -> bool:
        claimed.append((worker_id, nonce))
        return True

    verifier = WorkerAuthVerifier(load_secret, claim_nonce, _RateLimiter())
    timestamp = int(time.time())
    payload = {"run_id": "run-1"}
    signature = generate_hmac_signature(payload, "shared-secret", timestamp, "nonce-1")["X-Signature"]

    assert await verifier.verify_signature_async("worker-1", payload, timestamp, "nonce-1", signature)
    assert claimed == [("worker-1", "nonce-1")]


@pytest.mark.asyncio
async def test_invalid_signature_does_not_consume_nonce() -> None:
    claimed = False

    async def load_secret(_worker_id: str) -> str | None:
        return "shared-secret"

    async def claim_nonce(_worker_id: str, _nonce: str) -> bool:
        nonlocal claimed
        claimed = True
        return True

    verifier = WorkerAuthVerifier(load_secret, claim_nonce, _RateLimiter())

    assert not await verifier.verify_signature_async("worker-1", {}, int(time.time()), "nonce-1", "invalid")
    assert claimed is False


@pytest.mark.asyncio
async def test_replayed_nonce_is_rejected() -> None:
    async def load_secret(_worker_id: str) -> str | None:
        return "shared-secret"

    async def reject_nonce(_worker_id: str, _nonce: str) -> bool:
        return False

    verifier = WorkerAuthVerifier(load_secret, reject_nonce, _RateLimiter())
    timestamp = int(time.time())
    signature = generate_hmac_signature({}, "shared-secret", timestamp, "nonce-1")["X-Signature"]

    assert not await verifier.verify_signature_async("worker-1", {}, timestamp, "nonce-1", signature)


@pytest.mark.asyncio
async def test_rate_limit_uses_worker_scoped_distributed_key() -> None:
    limiter = _RateLimiter()
    verifier = WorkerAuthVerifier(rate_limiter=limiter)

    assert await verifier.check_rate_limit("worker-1")
    assert limiter.calls == [("worker-auth:worker-1", RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)]


@pytest.mark.asyncio
async def test_invalid_signature_does_not_consume_worker_rate_limit() -> None:
    async def load_secret(_worker_id: str) -> str | None:
        return "shared-secret"

    limiter = _RateLimiter()
    verifier = WorkerAuthVerifier(load_secret, rate_limiter=limiter)
    request = _request(
        {
            "X-Worker-ID": "worker-1",
            "X-Timestamp": str(int(time.time())),
            "X-Nonce": "nonce-1",
            "X-Signature": "invalid",
        },
        {},
    )

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(request)

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED
    assert limiter.calls == []


@pytest.mark.asyncio
async def test_unsigned_request_does_not_consume_claimed_worker_rate_limit() -> None:
    limiter = _RateLimiter()
    verifier = WorkerAuthVerifier(rate_limiter=limiter)
    request = _request({"X-Worker-ID": "worker-1"}, {})

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(request)

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED
    assert limiter.calls == []
