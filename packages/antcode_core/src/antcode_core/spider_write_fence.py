"""Atomic tombstone fences for Direct Spider Redis writes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS, LeaseStore
from antcode_core.application.services.workers.run_ownership_fence import ownership_token, run_owner_key


@dataclass(frozen=True)
class SpiderWriteIdentity:
    namespace: str
    worker_id: str
    lease_id: str
    run_id: str
    project_id: str

    def redis_keys(self) -> tuple[str, str, str]:
        lease = LeaseStore.LEASE_KEY_TEMPLATE.format(ns=self.namespace, worker_id=self.worker_id)
        revoked = LeaseStore.REVOKED_SET_TEMPLATE.format(ns=self.namespace, worker_id=self.worker_id)
        return lease, revoked, run_owner_key(self.run_id, self.namespace)

    @property
    def owner_token(self) -> str:
        return ownership_token(self.worker_id, self.lease_id)


_FENCE_LUA = r"""
local lease_key = KEYS[1]
local revoked_key = KEYS[2]
local owner_key = KEYS[3]
local tombstone = KEYS[4]
local worker_id = ARGV[1]
local lease_id = ARGV[2]
local owner_token = ARGV[3]
local retention_ms = tonumber(ARGV[4])
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
if redis.call('SISMEMBER', revoked_key, lease_id) == 1 then
    return redis.error_reply('SPIDER_LEASE_STALE')
end
if redis.call('HGET', lease_key, 'worker_id') ~= worker_id
   or redis.call('HGET', lease_key, 'lease_id') ~= lease_id
   or tonumber(redis.call('HGET', lease_key, 'expires_at_ms') or '0') <= now_ms
   or redis.call('PTTL', lease_key) <= retention_ms then
    return redis.error_reply('SPIDER_LEASE_STALE')
end
if redis.call('GET', owner_key) ~= owner_token then
    return redis.error_reply('SPIDER_RUN_NOT_OWNED')
end
if redis.call('EXISTS', tombstone) == 1 then
    return redis.error_reply('SPIDER_RUN_DELETED')
end
"""

_WRITE_META_LUA = (
    _FENCE_LUA
    + r"""
local meta = KEYS[5]
local markers = KEYS[6]
local index_key = KEYS[7]
local index_expiry_key = KEYS[8]
local run_id = ARGV[5]
local project_id = ARGV[6]
local ttl = tonumber(ARGV[7])
local field_count = tonumber(ARGV[8])
local function key_type(key)
    local result = redis.call('TYPE', key)
    if type(result) == 'table' then return result['ok'] end
    return result
end
local meta_type = key_type(meta)
local markers_type = key_type(markers)
local index_type = key_type(index_key)
local index_expiry_type = key_type(index_expiry_key)
if meta_type ~= 'none' and meta_type ~= 'hash' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH meta')
end
if markers_type ~= 'none' and markers_type ~= 'hash' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH markers')
end
if index_type ~= 'none' and index_type ~= 'zset' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH index')
end
if index_expiry_type ~= 'none' and index_expiry_type ~= 'zset' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH index_expiry')
end
local markers_were_empty = redis.call('HLEN', markers) == 0
local configured_project = redis.call('HGET', markers, '__project_id__')
local configured_ttl = redis.call('HGET', markers, '__ttl_seconds__')
if configured_project and configured_project ~= project_id then
    return redis.error_reply('SPIDER_PROJECT_CONFLICT')
end
if configured_ttl and tonumber(configured_ttl) ~= ttl then
    return redis.error_reply('SPIDER_RETENTION_CHANGED')
end
local command = {'HSET', meta}
for field_index = 0, field_count - 1 do
    table.insert(command, ARGV[9 + field_index * 2])
    table.insert(command, ARGV[10 + field_index * 2])
end
redis.call(unpack(command))
if not configured_project then redis.call('HSET', markers, '__project_id__', project_id) end
if not configured_ttl then redis.call('HSET', markers, '__ttl_seconds__', ttl) end
local now_seconds = now_ms / 1000
local expired_runs = redis.call('ZRANGEBYSCORE', index_expiry_key, '-inf', now_seconds)
if #expired_runs > 0 then
    redis.call('ZREM', index_key, unpack(expired_runs))
    redis.call('ZREM', index_expiry_key, unpack(expired_runs))
end
redis.call('ZADD', index_key, now_seconds, run_id)
if ttl > 0 then
    redis.call('ZADD', index_expiry_key, now_seconds + ttl, run_id)
else
    redis.call('ZREM', index_expiry_key, run_id)
end
redis.call('PERSIST', index_key)
redis.call('PERSIST', index_expiry_key)
if ttl > 0 then redis.call('EXPIRE', meta, ttl)
else redis.call('PERSIST', meta) end
if markers_were_empty and ttl > 0 then redis.call('EXPIRE', markers, ttl) end
if markers_were_empty and ttl == 0 then redis.call('PERSIST', markers) end
return 1
"""
)


async def write_fenced_spider_meta(
    redis: Any,
    meta_key: str,
    *,
    identity: SpiderWriteIdentity,
    tombstone_key: str,
    marker_key: str,
    index_key: str,
    index_expiry_key: str,
    fields: Mapping[Any, Any],
    ttl_seconds: int,
) -> None:
    if not fields:
        return
    lease_key, revoked_key, owner_key = identity.redis_keys()
    args: list[Any] = [
        identity.worker_id,
        identity.lease_id,
        identity.owner_token,
        LEASE_RECORD_RETENTION_MS,
        identity.run_id,
        identity.project_id,
        ttl_seconds,
        len(fields),
    ]
    for name, value in fields.items():
        args.extend((name, value))
    result = await redis.eval(
        _WRITE_META_LUA,
        8,
        lease_key,
        revoked_key,
        owner_key,
        tombstone_key,
        meta_key,
        marker_key,
        index_key,
        index_expiry_key,
        *args,
    )
    if int(result) != 1:
        raise RuntimeError(f"Spider meta Lua ACK 非法: {result!r}")


__all__ = [
    "SpiderWriteIdentity",
    "write_fenced_spider_meta",
]
