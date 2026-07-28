"""Atomic, idempotent Spider item writes shared by all transports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from loguru import logger
from redis.exceptions import NoScriptError

from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS
from antcode_core.spider_write_fence import SpiderWriteIdentity

_ITEM_FIELDS = (
    "item_id",
    "run_id",
    "project_id",
    "spider_name",
    "item_type",
    "data",
    "url",
    "timestamp",
    "sequence",
)
_LENGTH_PREFIX_BYTES = 8
_WRITE_RESULT_FIELDS = 3

_WRITE_ITEMS_LUA = r"""
local stream = KEYS[1]
local markers = KEYS[2]
local marker_order = KEYS[3]
local tombstone = KEYS[4]
local lease_key = KEYS[5]
local revoked_key = KEYS[6]
local owner_key = KEYS[7]
local index_key = KEYS[8]
local index_expiry_key = KEYS[9]
local worker_id = ARGV[1]
local lease_id = ARGV[2]
local owner_token = ARGV[3]
local retention_ms = tonumber(ARGV[4])
local project_id = ARGV[5]
local run_id = ARGV[6]
local max_len = tonumber(ARGV[7])
local ttl = tonumber(ARGV[8])
local item_count = tonumber(ARGV[9])
local field_names = {
    'item_id', 'run_id', 'project_id', 'spider_name', 'item_type',
    'data', 'url', 'timestamp', 'sequence'
}
local width = #field_names + 1
local inserted = 0
local duplicate_count = 0
local seen = {}
local new_offsets = {}
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local function key_type(key)
    local result = redis.call('TYPE', key)
    if type(result) == 'table' then return result['ok'] end
    return result
end
if redis.call('SISMEMBER', revoked_key, lease_id) == 1
   or redis.call('HGET', lease_key, 'worker_id') ~= worker_id
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
local stream_type = key_type(stream)
local markers_type = key_type(markers)
local order_type = key_type(marker_order)
local index_type = key_type(index_key)
local index_expiry_type = key_type(index_expiry_key)
if stream_type ~= 'none' and stream_type ~= 'stream' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH stream')
end
if markers_type ~= 'none' and markers_type ~= 'hash' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH markers')
end
if order_type ~= 'none' and order_type ~= 'zset' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH marker_order')
end
if index_type ~= 'none' and index_type ~= 'zset' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH index')
end
if index_expiry_type ~= 'none' and index_expiry_type ~= 'zset' then
    return redis.error_reply('SPIDER_KEY_TYPE_MISMATCH index_expiry')
end
local configured_max_len = redis.call('HGET', markers, '__max_len__')
local configured_ttl = redis.call('HGET', markers, '__ttl_seconds__')
local configured_project = redis.call('HGET', markers, '__project_id__')
if configured_max_len and tonumber(configured_max_len) ~= max_len then
    return redis.error_reply('SPIDER_RETENTION_CHANGED')
end
if configured_ttl and tonumber(configured_ttl) ~= ttl then
    return redis.error_reply('SPIDER_RETENTION_CHANGED')
end
if configured_project and configured_project ~= project_id then
    return redis.error_reply('SPIDER_PROJECT_CONFLICT')
end
for index = 0, item_count - 1 do
    local offset = 10 + index * width
    local digest = ARGV[offset]
    local item_id = ARGV[offset + 1]
    local marker_field = 'item:' .. item_id
    if seen[item_id] then
        if seen[item_id] ~= digest then
            return redis.error_reply('SPIDER_ITEM_ID_CONFLICT ' .. item_id)
        end
        duplicate_count = duplicate_count + 1
    else
        seen[item_id] = digest
        local existing = redis.call('HGET', markers, marker_field)
        if existing and existing ~= digest then
            return redis.error_reply('SPIDER_ITEM_ID_CONFLICT ' .. item_id)
        end
        if existing then duplicate_count = duplicate_count + 1
        else table.insert(new_offsets, offset) end
    end
end
local arrival = 0
if max_len > 0 and #new_offsets > 0 then
    arrival = redis.call('HINCRBY', markers, '__arrival__', #new_offsets) - #new_offsets
end
if not configured_max_len then redis.call('HSET', markers, '__max_len__', max_len) end
if not configured_ttl then redis.call('HSET', markers, '__ttl_seconds__', ttl) end
if not configured_project then redis.call('HSET', markers, '__project_id__', project_id) end
for _, offset in ipairs(new_offsets) do
    local digest = ARGV[offset]
    local item_id = ARGV[offset + 1]
    local marker_field = 'item:' .. item_id
    local command = {'XADD', stream}
    if max_len > 0 then
        table.insert(command, 'MAXLEN')
        table.insert(command, tostring(max_len))
    end
    table.insert(command, '*')
    for field_index, name in ipairs(field_names) do
        table.insert(command, name)
        table.insert(command, ARGV[offset + field_index])
    end
    redis.call(unpack(command))
    redis.call('HSET', markers, marker_field, digest)
    if max_len > 0 then
        arrival = arrival + 1
        redis.call('ZADD', marker_order, arrival, marker_field)
    end
    inserted = inserted + 1
end
if max_len > 0 then
    local excess = redis.call('ZCARD', marker_order) - max_len
    if excess > 0 then
        local expired = redis.call('ZRANGE', marker_order, 0, excess - 1)
        if #expired > 0 then
            redis.call('ZREM', marker_order, unpack(expired))
        end
    end
end
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
if ttl > 0 then
    redis.call('EXPIRE', stream, ttl)
    redis.call('EXPIRE', markers, ttl)
    redis.call('EXPIRE', marker_order, ttl)
else
    redis.call('PERSIST', stream)
    redis.call('PERSIST', markers)
    redis.call('PERSIST', marker_order)
end
return {item_count, inserted, duplicate_count}
"""
_WRITE_ITEMS_SHA = hashlib.sha1(_WRITE_ITEMS_LUA.encode(), usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class SpiderItemWriteResult:
    accepted: int
    inserted: int
    duplicates: int


class IdempotentSpiderItemWriter:
    """Write one batch with item-id deduplication and one Redis round trip."""

    def __init__(self, redis_client: Any, *, stream_max_len: int, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._stream_max_len = stream_max_len
        self._ttl_seconds = ttl_seconds

    async def write(
        self,
        stream_key: str,
        marker_key: str,
        marker_order_key: str,
        *,
        identity: SpiderWriteIdentity,
        tombstone_key: str,
        index_key: str,
        index_expiry_key: str,
        payloads: list[dict[str, Any]],
    ) -> SpiderItemWriteResult:
        if not payloads:
            return SpiderItemWriteResult(accepted=0, inserted=0, duplicates=0)
        lease_key, revoked_key, owner_key = identity.redis_keys()
        keys = (
            stream_key,
            marker_key,
            marker_order_key,
            tombstone_key,
            lease_key,
            revoked_key,
            owner_key,
            index_key,
            index_expiry_key,
        )
        raw = await self._run_script(keys, self._script_args(identity, payloads))
        result = self._parse_result(raw)
        if result.accepted != len(payloads):
            raise RuntimeError(f"SpiderData Lua ACK 数量不匹配: expected={len(payloads)} accepted={result.accepted}")
        return result

    def _script_args(self, identity: SpiderWriteIdentity, payloads: list[dict[str, Any]]) -> list[Any]:
        args: list[Any] = [
            identity.worker_id,
            identity.lease_id,
            identity.owner_token,
            LEASE_RECORD_RETENTION_MS,
            identity.project_id,
            identity.run_id,
            self._stream_max_len,
            self._ttl_seconds,
            len(payloads),
        ]
        for payload in payloads:
            args.append(_payload_digest(payload))
            args.extend(payload[name] for name in _ITEM_FIELDS)
        return args

    async def _run_script(self, keys: tuple[str, ...], args: list[Any]) -> Any:
        try:
            return await self._redis.evalsha(_WRITE_ITEMS_SHA, len(keys), *keys, *args)
        except NoScriptError:
            logger.warning("SpiderData 幂等脚本缓存失效，使用 EVAL 重新执行")
            return await self._redis.eval(_WRITE_ITEMS_LUA, len(keys), *keys, *args)

    @staticmethod
    def _parse_result(raw: Any) -> SpiderItemWriteResult:
        if not isinstance(raw, (list, tuple)) or len(raw) != _WRITE_RESULT_FIELDS:
            raise RuntimeError(f"SpiderData Lua 返回结构非法: {raw!r}")
        return SpiderItemWriteResult(accepted=int(raw[0]), inserted=int(raw[1]), duplicates=int(raw[2]))


def _payload_digest(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    for name in _ITEM_FIELDS:
        value = payload[name]
        encoded = value if isinstance(value, bytes) else str(value).encode()
        digest.update(len(encoded).to_bytes(_LENGTH_PREFIX_BYTES, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


__all__ = ["IdempotentSpiderItemWriter", "SpiderItemWriteResult"]
