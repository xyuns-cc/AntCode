"""B11: 限流必须排在 body 解析 / DB 查询 / 验签之前，且不可被换 worker_id 绕过。"""

import json
import time

import pytest
from antcode_core.common.security import WORKER_HTTP_SIGNATURE_HEADER, WORKER_HTTP_SIGNATURE_VERSION
from antcode_core.common.security import worker_auth as worker_auth_module
from antcode_core.common.security.worker_auth import (
    MAX_REQUEST_BODY_BYTES,
    MAX_WORKER_ID_LENGTH,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    SOURCE_RATE_LIMIT_REQUESTS,
    SOURCE_RATE_LIMIT_WINDOW_SECONDS,
    WorkerAuthVerifier,
)
from fastapi import HTTPException
from starlette.requests import Request

HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_CONTENT_TOO_LARGE = 413
HTTP_TOO_MANY_REQUESTS = 429

SIGNED_PATH = "/api/v1/workers/worker-1/direct-control/lease"
ATTACKER_IP = "203.0.113.9"
SOURCE_LIMIT_FOR_TEST = 3


class _RecordingRateLimiter:
    """按 key 计数的限流器，同时记录调用顺序（不 mock 被测函数本身）。"""

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.counts: dict[str, int] = {}
        self.calls: list[tuple[str, int, int]] = []

    async def is_allowed(self, identifier: str, limit: int, period: int) -> bool:
        self._events.append(f"rate-limit:{identifier}")
        self.calls.append((identifier, limit, period))
        self.counts[identifier] = self.counts.get(identifier, 0) + 1
        return self.counts[identifier] <= limit


def _recording_secret_loader(events: list[str], loaded: list[str]):
    async def load_secret(worker_id: str) -> str | None:
        events.append(f"db:{worker_id}")
        loaded.append(worker_id)
        return "shared-secret"

    return load_secret


def _request(
    headers: dict[str, str],
    *,
    body: bytes = b"{}",
    path: str = SIGNED_PATH,
    client: tuple[str, int] | None = (ATTACKER_IP, 51234),
) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(name.lower().encode(), value.encode()) for name, value in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": raw_headers,
        "client": client,
    }
    return Request(scope, receive)


def _headers(worker_id: str = "worker-1", **overrides: str) -> dict[str, str]:
    """构造签名头齐全但签名值无效的请求头（未认证攻击者视角）。"""
    headers = {
        "X-Worker-ID": worker_id,
        "X-Timestamp": str(int(time.time())),
        "X-Nonce": "nonce-1",
        "X-Signature": "invalid-signature",
        WORKER_HTTP_SIGNATURE_HEADER: WORKER_HTTP_SIGNATURE_VERSION,
    }
    headers.update(overrides)
    return headers


@pytest.mark.asyncio
async def test_invalid_signature_consumes_rate_limit_before_database() -> None:
    events: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, []), rate_limiter=limiter)

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(_request(_headers()))

    assert exc_info.value.status_code == HTTP_UNAUTHORIZED
    # 验签失败同样消耗配额，攻击者不能靠垃圾签名白嫖。
    assert limiter.calls == [
        (f"worker-auth:source:{ATTACKER_IP}", SOURCE_RATE_LIMIT_REQUESTS, SOURCE_RATE_LIMIT_WINDOW_SECONDS),
        (f"worker-auth:worker:worker-1:{ATTACKER_IP}", RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS),
    ]
    # 限流两层都排在 PostgreSQL 查询之前。
    assert events == [
        f"rate-limit:worker-auth:source:{ATTACKER_IP}",
        f"rate-limit:worker-auth:worker:worker-1:{ATTACKER_IP}",
        "db:worker-1",
    ]


@pytest.mark.asyncio
async def test_rate_limited_request_never_reaches_database_or_body_parsing() -> None:
    events: list[str] = []
    loaded: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, loaded), rate_limiter=limiter)
    # 配额耗尽后再发一发；body 是非法 JSON，若被解析会返回 400 而不是 429。
    limiter.counts[f"worker-auth:source:{ATTACKER_IP}"] = SOURCE_RATE_LIMIT_REQUESTS

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(_request(_headers(), body=b"not-json"))

    assert exc_info.value.status_code == HTTP_TOO_MANY_REQUESTS
    assert loaded == []
    assert "db:worker-1" not in events


@pytest.mark.asyncio
async def test_worker_id_rotation_cannot_bypass_source_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(worker_auth_module, "SOURCE_RATE_LIMIT_REQUESTS", SOURCE_LIMIT_FOR_TEST)
    events: list[str] = []
    loaded: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, loaded), rate_limiter=limiter)
    statuses: list[int] = []

    for index in range(SOURCE_LIMIT_FOR_TEST + 1):
        with pytest.raises(HTTPException) as exc_info:
            await verifier.verify_request(_request(_headers(f"rotated-{index}")))
        statuses.append(exc_info.value.status_code)

    # 前 N 发按来源配额放行到验签（401），第 N+1 发被来源桶挡下（429）。
    assert statuses == [HTTP_UNAUTHORIZED] * SOURCE_LIMIT_FOR_TEST + [HTTP_TOO_MANY_REQUESTS]
    # 来源桶 key 里没有 worker_id，换 ID 不产生新桶。
    assert limiter.counts[f"worker-auth:source:{ATTACKER_IP}"] == SOURCE_LIMIT_FOR_TEST + 1
    assert loaded == [f"rotated-{index}" for index in range(SOURCE_LIMIT_FOR_TEST)]


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_reading() -> None:
    events: list[str] = []
    loaded: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, loaded), rate_limiter=limiter)
    headers = _headers()
    headers["Content-Length"] = str(MAX_REQUEST_BODY_BYTES + 1)

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(_request(headers))

    assert exc_info.value.status_code == HTTP_CONTENT_TOO_LARGE
    assert loaded == []


@pytest.mark.asyncio
async def test_oversized_streamed_body_is_rejected_not_truncated() -> None:
    events: list[str] = []
    loaded: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, loaded), rate_limiter=limiter)
    oversized = json.dumps({"payload": "x" * (MAX_REQUEST_BODY_BYTES + 1)}).encode()

    with pytest.raises(HTTPException) as exc_info:
        # 分块传输没有 Content-Length，必须按实际字节数拒绝。
        await verifier.verify_request(_request(_headers(), body=oversized))

    assert exc_info.value.status_code == HTTP_CONTENT_TOO_LARGE
    assert loaded == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worker_id",
    [
        "w" * (MAX_WORKER_ID_LENGTH + 1),
        "worker:1",
        "worker*",
        "worker 1",
        "worker\n1",
        "../worker",
        "worker{1}",
    ],
)
async def test_illegal_worker_id_is_rejected_before_touching_redis(worker_id: str) -> None:
    events: list[str] = []
    loaded: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, loaded), rate_limiter=limiter)

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(_request(_headers(worker_id)))

    assert exc_info.value.status_code == HTTP_BAD_REQUEST
    # 非法 worker_id 绝不进入 Redis key，也不打 DB。
    assert limiter.calls == []
    assert loaded == []


@pytest.mark.asyncio
async def test_request_without_resolvable_source_is_rejected() -> None:
    events: list[str] = []
    loaded: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, loaded), rate_limiter=limiter)

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(_request(_headers(), client=None))

    assert exc_info.value.status_code == HTTP_BAD_REQUEST
    assert limiter.calls == []
    assert loaded == []


@pytest.mark.asyncio
async def test_forwarded_for_is_ignored_without_trusted_proxy(monkeypatch) -> None:
    monkeypatch.delenv("ANTCODE_TRUSTED_PROXIES", raising=False)
    events: list[str] = []
    limiter = _RecordingRateLimiter(events)
    verifier = WorkerAuthVerifier(_recording_secret_loader(events, []), rate_limiter=limiter)
    headers = _headers()
    headers["X-Forwarded-For"] = "198.51.100.7"

    with pytest.raises(HTTPException):
        await verifier.verify_request(_request(headers))

    # 伪造 XFF 不能换来新的限流桶。
    assert limiter.calls[0][0] == f"worker-auth:source:{ATTACKER_IP}"
