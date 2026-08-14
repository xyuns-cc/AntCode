"""Worker HTTP 边界：``verify_request`` 的拒绝路径与限流配额消耗。

凭据层（``verify_signature_async`` / ``check_rate_limit``）的用例见
``test_worker_auth_distributed``；共用的请求/签名搭建见 ``worker_auth_support``。
"""

import pytest
from antcode_core.common.security import WORKER_HTTP_SIGNATURE_HEADER, generate_hmac_signature
from antcode_core.common.security.worker_auth import WorkerAuthVerifier
from fastapi import HTTPException

from tests.unit.core.worker_auth_support import (
    CLIENT_IP,
    HTTP_UNAUTHORIZED,
    SHARED_SECRET,
    SIGNED_PATH,
    WORKER_ID,
    RateLimiterSpy,
    build_request,
    load_shared_secret,
    signature_headers,
)

_WORKER_ID_HEADER = {"X-Worker-ID": WORKER_ID}


@pytest.mark.asyncio
async def test_invalid_signature_still_consumes_worker_rate_limit() -> None:
    limiter = RateLimiterSpy()
    verifier = WorkerAuthVerifier(load_shared_secret, rate_limiter=limiter)
    headers = {
        **_WORKER_ID_HEADER,
        **signature_headers({}),
        "X-Signature": "invalid",
    }

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(build_request(headers, {}))

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED
    # B11: 限流已前置到验签之前，未认证请求同样消耗配额。
    assert [identifier for identifier, _limit, _period in limiter.calls] == [
        f"worker-auth:source:{CLIENT_IP}",
        f"worker-auth:worker:{WORKER_ID}:{CLIENT_IP}",
    ]


@pytest.mark.asyncio
async def test_legacy_signature_without_version_is_rejected() -> None:
    headers = {**_WORKER_ID_HEADER, **signature_headers({})}
    headers.pop(WORKER_HTTP_SIGNATURE_HEADER)
    verifier = WorkerAuthVerifier(rate_limiter=RateLimiterSpy())

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(build_request(headers, {}))

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED


@pytest.mark.asyncio
async def test_unsupported_signature_version_is_rejected() -> None:
    headers = {**_WORKER_ID_HEADER, **signature_headers({})}
    headers[WORKER_HTTP_SIGNATURE_HEADER] = "1"
    verifier = WorkerAuthVerifier(rate_limiter=RateLimiterSpy())

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(build_request(headers, {}))

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("PUT", SIGNED_PATH, {}),
        ("POST", "/api/v1/workers/worker-1/direct-control/deregister", {}),
        ("POST", SIGNED_PATH, {"semantically": "different"}),
    ],
)
async def test_signature_cannot_replay_across_request_domain(method: str, path: str, payload: dict) -> None:
    claimed = False

    async def claim_nonce(_worker_id: str, _nonce: str) -> bool:
        nonlocal claimed
        claimed = True
        return True

    headers = {**_WORKER_ID_HEADER, **signature_headers({})}
    verifier = WorkerAuthVerifier(load_shared_secret, claim_nonce, RateLimiterSpy())

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(build_request(headers, payload, method=method, path=path))

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED
    assert claimed is False


@pytest.mark.asyncio
async def test_signature_binds_exact_body_bytes_not_json_semantics() -> None:
    signed_body = b'{"value":1}'
    transmitted_body = b'{ "value": 1 }'
    signature = generate_hmac_signature(
        signed_body,
        SHARED_SECRET,
        method="POST",
        path=SIGNED_PATH,
        nonce="nonce-exact-body",
    )
    headers = {**_WORKER_ID_HEADER, **signature}
    verifier = WorkerAuthVerifier(load_shared_secret, rate_limiter=RateLimiterSpy())

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(build_request(headers, {}, raw_body=transmitted_body))

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED


@pytest.mark.asyncio
async def test_unsigned_request_does_not_consume_claimed_worker_rate_limit() -> None:
    limiter = RateLimiterSpy()
    verifier = WorkerAuthVerifier(rate_limiter=limiter)

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(build_request(dict(_WORKER_ID_HEADER), {}))

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED
    assert limiter.calls == []
