"""Distributed Worker HMAC authentication."""

from __future__ import annotations

import hmac
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import HTTPException, Request, status
from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.security import (
    WORKER_HTTP_SIGNATURE_HEADER,
    WORKER_HTTP_SIGNATURE_VERSION,
    verify_hmac_signature,
)
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.network_source import extract_client_ip
from antcode_core.common.security.secret_box import secret_box
from antcode_core.infrastructure.redis.rate_limiter import redis_rate_limiter

TIMESTAMP_TOLERANCE_SECONDS = 300
NONCE_EXPIRY_SECONDS = 600
RATE_LIMIT_REQUESTS = 1000
RATE_LIMIT_WINDOW_SECONDS = 60
# 来源维度配额：key 里**不含** worker_id，攻击者换 X-Worker-ID 换不到新桶。
# 按“一个来源最多承载几个 Worker”折算，容纳同主机多 Worker 的正常部署。
SOURCE_RATE_LIMIT_WORKERS_PER_SOURCE = 3
SOURCE_RATE_LIMIT_REQUESTS = RATE_LIMIT_REQUESTS * SOURCE_RATE_LIMIT_WORKERS_PER_SOURCE
SOURCE_RATE_LIMIT_WINDOW_SECONDS = 60
MAX_NONCE_LENGTH = 128
# worker_id 由请求方声明且会拼进 Redis key，必须先约束长度与字符集。
MAX_WORKER_ID_LENGTH = 64
WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
# 未认证端点上的 body 上限：注册 / 心跳载荷远小于 1 MiB，超限显式 413。
MAX_REQUEST_BODY_BYTES = 1024 * 1024
TRUSTED_PROXIES_ENV = "ANTCODE_TRUSTED_PROXIES"


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


def resolve_request_source(request: Request) -> str:
    """解析限流域标识（未认证阶段唯一不可由请求方伪造的维度）。

    只有当 socket 对端命中 ``ANTCODE_TRUSTED_PROXIES`` 白名单时才采信
    ``X-Forwarded-For`` / ``X-Real-IP``；未配置白名单时转发头一律不可信，
    直接以 socket 对端标识为准。无法确定来源时显式 400，不做任何回退。
    """
    peer = request.client.host if request.client else ""
    if not peer:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法确定请求来源")
    trusted_proxies = os.getenv(TRUSTED_PROXIES_ENV, "")
    if not trusted_proxies:
        return peer
    try:
        return extract_client_ip(
            peer,
            request.headers.get("X-Forwarded-For", ""),
            request.headers.get("X-Real-IP", ""),
            trusted_proxies=trusted_proxies,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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

    async def check_rate_limit(self, worker_id: str, source: str) -> bool:
        """两层配额都必须通过，任意一层超限即拒绝。

        - 来源层 ``worker-auth:source:{source}``：key 里不含 worker_id，
          攻击者遍历 X-Worker-ID 也只会消耗同一个桶，无法绕过。
        - Worker 层 ``worker-auth:worker:{worker_id}:{source}``：保留每
          Worker 配额语义，并绑定来源，避免单一远程来源冒用他人 worker_id
          把合法 Worker 的桶打爆（与 login_guard 的用户名级限流同构）。
        """
        source_allowed = await self._rate_limiter.is_allowed(
            f"worker-auth:source:{source}",
            SOURCE_RATE_LIMIT_REQUESTS,
            SOURCE_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not source_allowed:
            return False
        return await self._rate_limiter.is_allowed(
            f"worker-auth:worker:{worker_id}:{source}",
            RATE_LIMIT_REQUESTS,
            RATE_LIMIT_WINDOW_SECONDS,
        )

    async def verify_signature_async(
        self,
        worker_id: str,
        *,
        method: str,
        path: str,
        body: bytes,
        timestamp: int,
        nonce: str,
        signature: str,
        version: str,
    ) -> bool:
        if version != WORKER_HTTP_SIGNATURE_VERSION:
            logger.warning(f"Worker HMAC 签名版本无效: {worker_id}")
            return False
        secret = await self._secret_loader(worker_id)
        if secret is None:
            logger.warning(f"Worker HMAC secret 不存在: {worker_id}")
            return False
        if not self._timestamp_is_valid(timestamp):
            logger.warning(f"Worker HMAC 时间戳过期: {worker_id}")
            return False

        if not verify_hmac_signature(
            body,
            secret,
            method=method,
            path=path,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            version=version,
            max_age_seconds=TIMESTAMP_TOLERANCE_SECONDS,
        ):
            logger.warning(f"Worker HMAC 签名无效: {worker_id}")
            return False
        if not await self._nonce_claimer(worker_id, nonce):
            logger.warning(f"Worker HMAC nonce 重放: {worker_id}")
            return False
        return True

    async def verify_request(self, request: Request) -> dict[str, Any]:
        """按代价从低到高逐级拒绝，限流排在一切昂贵操作之前。

        顺序：请求头校验（纯内存）→ worker_id 格式校验 → 来源解析 → 限流
        （Redis）→ body 读取与 JSON 解析 → PostgreSQL 取 secret + 验签。
        验签失败也已经消耗过配额，未认证攻击者无法靠垃圾签名白嫖 DB 查询。
        请求头缺失/版本不对在限流之前拒绝，这类请求连一次 Redis 往返都不值得。
        """
        worker_id = request.headers.get("X-Worker-ID", "").strip()
        timestamp, nonce, signature, version = self._signature_headers(request, worker_id)
        self._validate_worker_id(worker_id)
        source = resolve_request_source(request)
        if not await self.check_rate_limit(worker_id, source):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求频率过高")
        body = await self._request_body(request)
        verified = await self.verify_signature_async(
            worker_id,
            method=request.method,
            path=request.url.path,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            version=version,
        )
        if not verified:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="签名验证失败")
        return {"worker_id": worker_id, "verified": True, "signature_verified": True}

    @staticmethod
    def _validate_worker_id(worker_id: str) -> None:
        """worker_id 会直接拼进 Redis key，先约束长度与字符集再使用。"""
        if len(worker_id) > MAX_WORKER_ID_LENGTH or not WORKER_ID_PATTERN.match(worker_id):
            logger.warning("Worker ID 格式非法，拒绝请求")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Worker ID 格式错误")

    @staticmethod
    def _signature_headers(request: Request, worker_id: str) -> tuple[int, str, str, str]:
        timestamp_text = request.headers.get("X-Timestamp", "").strip()
        nonce = request.headers.get("X-Nonce", "").strip()
        signature = request.headers.get("X-Signature", "").strip()
        version = request.headers.get(WORKER_HTTP_SIGNATURE_HEADER, "").strip()
        if not worker_id or not timestamp_text or not nonce or not signature or not version:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少签名信息")
        if version != WORKER_HTTP_SIGNATURE_VERSION:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="不支持的签名版本")
        if len(nonce) > MAX_NONCE_LENGTH:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nonce 格式错误")
        try:
            return int(timestamp_text), nonce, signature, version
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="时间戳格式错误") from exc

    @staticmethod
    def _reject_oversized_body(size: int) -> None:
        """超过上限直接 413，不截断、不静默丢弃。"""
        if size > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"请求体超过上限 {MAX_REQUEST_BODY_BYTES} 字节",
            )

    @staticmethod
    def _declared_body_size(content_length: str) -> int:
        """分块传输没有 Content-Length，返回 0 交给读后的实际长度判定。"""
        declared = content_length.strip()
        if not declared:
            return 0
        try:
            return int(declared)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Content-Length 格式错误") from exc

    @staticmethod
    async def _request_body(request: Request) -> bytes:
        # 先按声明长度拒绝，避免为超大 body 付出完整读取与解析代价。
        declared_size = WorkerAuthVerifier._declared_body_size(request.headers.get("content-length", ""))
        WorkerAuthVerifier._reject_oversized_body(declared_size)
        body = await request.body()
        WorkerAuthVerifier._reject_oversized_body(len(body))
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体必须是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体必须是 JSON 对象")
        return body


worker_auth_verifier = WorkerAuthVerifier()


async def verify_worker_request_with_signature(request: Request) -> dict[str, Any]:
    return await worker_auth_verifier.verify_request(request)


__all__ = [
    "WorkerAuthVerifier",
    "claim_worker_nonce",
    "load_worker_secret",
    "resolve_request_source",
    "verify_worker_request_with_signature",
    "worker_auth_verifier",
]
