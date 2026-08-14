"""Worker HMAC 认证测试的共享搭建，不含断言。

``verify_signature_async``（凭据层）与 ``verify_request``（HTTP 边界层）两组
用例都要造同一套签名头 / ASGI 请求 / 限流探针，集中在这里避免两个测试文件
各维护一份。
"""

from __future__ import annotations

import json
import time

from antcode_core.common.security import generate_hmac_signature
from starlette.requests import Request

HTTP_UNAUTHORIZED = 401
CLIENT_IP = "203.0.113.9"
SIGNED_PATH = "/api/v1/workers/worker-1/direct-control/lease"
SHARED_SECRET = "shared-secret"
DEFAULT_NONCE = "nonce-1"
WORKER_ID = "worker-1"

_CLIENT_PORT = 51234


class RateLimiterSpy:
    """记录每次限流调用的 (identifier, limit, period)，用于断言限流域推导。"""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, int, int]] = []

    async def is_allowed(self, identifier: str, limit: int, period: int) -> bool:
        self.calls.append((identifier, limit, period))
        return self.allowed


def body_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode()


async def load_shared_secret(_worker_id: str) -> str | None:
    return SHARED_SECRET


def signature_headers(
    payload: dict,
    *,
    path: str = SIGNED_PATH,
    nonce: str = DEFAULT_NONCE,
) -> dict[str, str]:
    return generate_hmac_signature(
        body_bytes(payload),
        SHARED_SECRET,
        method="POST",
        path=path,
        timestamp=int(time.time()),
        nonce=nonce,
    )


def build_request(
    headers: dict[str, str],
    payload: dict,
    *,
    method: str = "POST",
    path: str = SIGNED_PATH,
    raw_body: bytes | None = None,
) -> Request:
    body = body_bytes(payload) if raw_body is None else raw_body
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(name.lower().encode(), value.encode()) for name, value in headers.items()]
    # 限流域来自 socket 对端，scope 必须带 client，否则 verify_request 会显式 400。
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
        "client": (CLIENT_IP, _CLIENT_PORT),
    }
    return Request(scope, receive)
