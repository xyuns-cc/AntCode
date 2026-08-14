"""Atomic lease-fenced storage for Gateway runtime settlements."""

from __future__ import annotations

import hashlib
from typing import Any

from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS, LeaseConflictError
from antcode_core.infrastructure.redis import redis_namespace

_CREATE_FENCED_SETTLEMENT_LUA = """
local current_lease = redis.call('HGET', KEYS[1], 'lease_id')
local lease_ttl = redis.call('PTTL', KEYS[1])
local expires_at_ms = tonumber(redis.call('HGET', KEYS[1], 'expires_at_ms') or '0')
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
if redis.call('SISMEMBER', KEYS[3], ARGV[1]) == 1
   or current_lease ~= ARGV[1]
   or expires_at_ms <= now_ms
   or lease_ttl <= tonumber(ARGV[5]) then
    return {-2, ''}
end
local stored = redis.call('GET', KEYS[2])
if stored then
    if stored ~= ARGV[2] and stored ~= ARGV[3] then
        return {-1, stored}
    end
    redis.call('PEXPIRE', KEYS[2], ARGV[4])
    return {0, stored}
end
redis.call('SET', KEYS[2], ARGV[2], 'PX', ARGV[4])
return {1, ARGV[2]}
"""


async def persist_fenced_settlement_marker(
    redis: Any,
    *,
    event_id: str,
    worker_id: str,
    lease_id: str,
    fingerprint: str,
    marker: str,
    ttl_seconds: int,
) -> None:
    result = await redis.eval(
        _CREATE_FENCED_SETTLEMENT_LUA,
        3,
        _lease_key(worker_id),
        settlement_key(event_id),
        _revoked_key(worker_id),
        lease_id,
        marker,
        fingerprint,
        ttl_seconds * 1000,
        LEASE_RECORD_RETENTION_MS,
    )
    outcome = _outcome(result)
    if outcome == -2:
        raise LeaseConflictError(worker_id, lease_id, "运行时控制结果提交时 lease 已切代")
    if outcome == -1:
        raise ValueError("控制事件已使用不同结果完成")
    if outcome not in {0, 1}:
        raise RuntimeError(f"运行时控制 settlement CAS 返回未知状态: {outcome}")


def settlement_key(event_id: str) -> str:
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return f"{{{redis_namespace()}}}:control:settlement:{digest}"


def _lease_key(worker_id: str) -> str:
    return f"{{{redis_namespace()}}}:lease:data:{worker_id}"


def _revoked_key(worker_id: str) -> str:
    return f"{{{redis_namespace()}}}:lease:revoked:{worker_id}"


def _outcome(result: Any) -> int:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise RuntimeError("运行时控制 settlement CAS 响应无效")
    return int(result[0])
