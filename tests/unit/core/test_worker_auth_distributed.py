"""Worker 凭据层：签名校验、nonce 消费与分布式限流键的推导。

HTTP 边界层（``verify_request``）的用例见 ``test_worker_auth_request_guard``；
两边共用的请求/签名搭建见 ``worker_auth_support``。
"""

import time

import pytest
from antcode_core.common.security import WORKER_HTTP_SIGNATURE_HEADER, generate_hmac_signature
from antcode_core.common.security.worker_auth import (
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    SOURCE_RATE_LIMIT_REQUESTS,
    SOURCE_RATE_LIMIT_WINDOW_SECONDS,
    WorkerAuthVerifier,
)
from antcode_core.common.security.worker_auth_reasons import WorkerAuthReason

from tests.unit.core.worker_auth_support import (
    CLIENT_IP,
    SHARED_SECRET,
    SIGNED_PATH,
    WORKER_ID,
    RateLimiterSpy,
    body_bytes,
    load_shared_secret,
)


@pytest.mark.asyncio
async def test_signature_uses_shared_secret_and_nonce_services() -> None:
    claimed: list[tuple[str, str]] = []

    async def load_secret(worker_id: str) -> str | None:
        return SHARED_SECRET if worker_id == WORKER_ID else None

    async def claim_nonce(worker_id: str, nonce: str) -> bool:
        claimed.append((worker_id, nonce))
        return True

    verifier = WorkerAuthVerifier(load_secret, claim_nonce, RateLimiterSpy())
    timestamp = int(time.time())
    payload = {"run_id": "run-1"}
    signature_headers = generate_hmac_signature(
        body_bytes(payload),
        SHARED_SECRET,
        method="POST",
        path=SIGNED_PATH,
        timestamp=timestamp,
        nonce="nonce-1",
    )

    reason = await verifier.verify_signature_async(
        WORKER_ID,
        method="POST",
        path=SIGNED_PATH,
        body=body_bytes(payload),
        timestamp=timestamp,
        nonce="nonce-1",
        signature=signature_headers["X-Signature"],
        version=signature_headers[WORKER_HTTP_SIGNATURE_HEADER],
    )
    assert reason is WorkerAuthReason.OK
    assert claimed == [(WORKER_ID, "nonce-1")]


@pytest.mark.asyncio
async def test_invalid_signature_does_not_consume_nonce() -> None:
    claimed = False

    async def claim_nonce(_worker_id: str, _nonce: str) -> bool:
        nonlocal claimed
        claimed = True
        return True

    verifier = WorkerAuthVerifier(load_shared_secret, claim_nonce, RateLimiterSpy())

    reason = await verifier.verify_signature_async(
        WORKER_ID,
        method="POST",
        path=SIGNED_PATH,
        body=body_bytes({}),
        timestamp=int(time.time()),
        nonce="nonce-1",
        signature="invalid",
        version="2",
    )
    assert reason is WorkerAuthReason.SIGNATURE_INVALID
    assert claimed is False


@pytest.mark.asyncio
async def test_replayed_nonce_is_rejected() -> None:
    async def reject_nonce(_worker_id: str, _nonce: str) -> bool:
        return False

    verifier = WorkerAuthVerifier(load_shared_secret, reject_nonce, RateLimiterSpy())
    timestamp = int(time.time())
    headers = generate_hmac_signature(
        body_bytes({}),
        SHARED_SECRET,
        method="POST",
        path=SIGNED_PATH,
        timestamp=timestamp,
        nonce="nonce-1",
    )

    reason = await verifier.verify_signature_async(
        WORKER_ID,
        method="POST",
        path=SIGNED_PATH,
        body=body_bytes({}),
        timestamp=timestamp,
        nonce="nonce-1",
        signature=headers["X-Signature"],
        version=headers[WORKER_HTTP_SIGNATURE_HEADER],
    )
    assert reason is WorkerAuthReason.NONCE_REPLAY


@pytest.mark.asyncio
async def test_rate_limit_uses_source_and_worker_scoped_distributed_keys() -> None:
    limiter = RateLimiterSpy()
    verifier = WorkerAuthVerifier(rate_limiter=limiter)

    assert await verifier.check_rate_limit(WORKER_ID, CLIENT_IP)
    # 来源桶不含 worker_id，攻击者换 X-Worker-ID 换不到新桶；Worker 桶再绑定来源。
    assert limiter.calls == [
        (f"worker-auth:source:{CLIENT_IP}", SOURCE_RATE_LIMIT_REQUESTS, SOURCE_RATE_LIMIT_WINDOW_SECONDS),
        (f"worker-auth:worker:{WORKER_ID}:{CLIENT_IP}", RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS),
    ]
