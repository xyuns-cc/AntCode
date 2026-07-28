"""Distributed Worker HMAC authentication."""

from __future__ import annotations

import hmac
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import HTTPException, Request, status
from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.security import constant_time_compare, generate_hmac_signature
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.secret_box import secret_box
from antcode_core.infrastructure.redis.rate_limiter import redis_rate_limiter

TIMESTAMP_TOLERANCE_SECONDS = 300
NONCE_EXPIRY_SECONDS = 600
RATE_LIMIT_REQUESTS = 1000
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_NONCE_LENGTH = 128


class RateLimiter(Protocol):
    async def is_allowed(self, identifier: str, limit: int, period: int) -> bool: ...


SecretLoader = Callable[[str], Awaitable[str | None]]
NonceClaimer = Callable[[str, str], Awaitable[bool]]


async def load_worker_secret(worker_id: str) -> str | None:
    """Load and integrity-check a Worker HMAC secret from PostgreSQL."""
    from antcode_core.domain.models.worker import Worker

    worker = await Worker.get_or_none(public_id=worker_id).only(
        "secret_key_encrypted",
        "secret_key_hash",
    )
    if worker is None or not worker.secret_key_encrypted or not worker.secret_key_hash:
        return None
    secret = secret_box.decrypt(worker.secret_key_encrypted)
    if not hmac.compare_digest(hash_api_key(secret), worker.secret_key_hash):
        raise RuntimeError(f"Worker HMAC secret integrity check failed: {worker_id}")
    return secret


async def claim_worker_nonce(worker_id: str, nonce: str) -> bool:
    """Atomically claim a nonce in Redis; dependency failures are fatal."""
    from antcode_core.infrastructure.redis.client import get_redis_client

    redis = await get_redis_client()
    key = f"{settings.REDIS_NAMESPACE}:worker-auth:nonce:{worker_id}:{nonce}"
    claimed = await redis.set(key, "1", nx=True, ex=NONCE_EXPIRY_SECONDS)
    return bool(claimed)


class WorkerAuthVerifier:
    """Verify Worker signatures using shared PostgreSQL and Redis state."""

    def __init__(
        self,
        secret_loader: SecretLoader = load_worker_secret,
        nonce_claimer: NonceClaimer = claim_worker_nonce,
        rate_limiter: RateLimiter = redis_rate_limiter,
    ) -> None:
        self._secret_loader = secret_loader
        self._nonce_claimer = nonce_claimer
        self._rate_limiter = rate_limiter

    @staticmethod
    def _timestamp_is_valid(timestamp: int) -> bool:
        return abs(int(time.time()) - timestamp) <= TIMESTAMP_TOLERANCE_SECONDS

    async def check_rate_limit(self, worker_id: str) -> bool:
        return await self._rate_limiter.is_allowed(
            f"worker-auth:{worker_id}",
            RATE_LIMIT_REQUESTS,
            RATE_LIMIT_WINDOW_SECONDS,
        )

    async def verify_signature_async(
        self,
        worker_id: str,
        payload: dict[str, Any],
        timestamp: int,
        nonce: str,
        signature: str,
    ) -> bool:
        secret = await self._secret_loader(worker_id)
        if secret is None:
            logger.warning(f"Worker HMAC secret 不存在: {worker_id}")
            return False
        if not self._timestamp_is_valid(timestamp):
            logger.warning(f"Worker HMAC 时间戳过期: {worker_id}")
            return False

        expected = generate_hmac_signature(payload, secret, timestamp, nonce)["X-Signature"]
        if not constant_time_compare(signature, expected):
            logger.warning(f"Worker HMAC 签名无效: {worker_id}")
            return False
        if not await self._nonce_claimer(worker_id, nonce):
            logger.warning(f"Worker HMAC nonce 重放: {worker_id}")
            return False
        return True

    async def verify_request(self, request: Request) -> dict[str, Any]:
        worker_id = request.headers.get("X-Worker-ID", "").strip()
        timestamp, nonce, signature = self._signature_headers(request, worker_id)
        payload = await self._request_payload(request)
        verified = await self.verify_signature_async(worker_id, payload, timestamp, nonce, signature)
        if not verified:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="签名验证失败")
        if not await self.check_rate_limit(worker_id):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求频率过高")
        return {"worker_id": worker_id, "verified": True, "signature_verified": True}

    @staticmethod
    def _signature_headers(request: Request, worker_id: str) -> tuple[int, str, str]:
        timestamp_text = request.headers.get("X-Timestamp", "").strip()
        nonce = request.headers.get("X-Nonce", "").strip()
        signature = request.headers.get("X-Signature", "").strip()
        if not worker_id or not timestamp_text or not nonce or not signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少签名信息")
        if len(nonce) > MAX_NONCE_LENGTH:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nonce 格式错误")
        try:
            return int(timestamp_text), nonce, signature
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="时间戳格式错误") from exc

    @staticmethod
    async def _request_payload(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体必须是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体必须是 JSON 对象")
        return payload


worker_auth_verifier = WorkerAuthVerifier()


async def verify_worker_request_with_signature(request: Request) -> dict[str, Any]:
    return await worker_auth_verifier.verify_request(request)


__all__ = [
    "WorkerAuthVerifier",
    "claim_worker_nonce",
    "load_worker_secret",
    "verify_worker_request_with_signature",
    "worker_auth_verifier",
]
