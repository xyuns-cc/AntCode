"""Redis Lua programs for the Worker lease state machine."""

from typing import Any

# SWEEP_DELETE_LUA 固定返回 {deleted_flag, doomed_lease_id} 两个元素。
SWEEP_DELETE_RESULT_ARITY = 2

GRANT_LUA = r"""
local lease_key, revoked_key, expiring_key = KEYS[1], KEYS[2], KEYS[3]
local active_key, seq_key, lifecycle_key = KEYS[4], KEYS[5], KEYS[6]
local worker_id, current_lease_id, new_lease_id = ARGV[1], ARGV[2], ARGV[3]
local ttl_ms, record_retention_ms = tonumber(ARGV[4]), tonumber(ARGV[5])
local metrics_json, capabilities_json = ARGV[6], ARGV[7]
if redis.call('EXISTS', lifecycle_key) == 1 then
    return {'', '', '', 'ineligible'}
end
if ttl_ms == nil or ttl_ms < 1 then
    return redis.error_reply('lease ttl_ms must be positive')
end
if record_retention_ms == nil or record_retention_ms < 1 then
    return redis.error_reply('lease record_retention_ms must be positive')
end
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local expires_at_ms = now_ms + ttl_ms
if current_lease_id ~= '' and redis.call('SISMEMBER', revoked_key, current_lease_id) == 1 then
    return {'', '', '', 'revoked'}
end
local stored_id = redis.call('HGET', lease_key, 'lease_id')
local stored_expires = tonumber(redis.call('HGET', lease_key, 'expires_at_ms') or '0')
local stored_granted = tonumber(redis.call('HGET', lease_key, 'granted_at_ms') or '0')
local stored_sequence = tonumber(redis.call('HGET', lease_key, 'sequence') or '0')
if stored_id and redis.call('SISMEMBER', revoked_key, stored_id) == 1 then
    return {'', '', '', 'revoked'}
end
local final_id, final_granted_ms, final_sequence, outcome
if stored_id and stored_expires > now_ms then
    if current_lease_id == '' or stored_id ~= current_lease_id then
        return {'', '', '', 'conflict'}
    end
    if capabilities_json ~= (redis.call('HGET', lease_key, 'capabilities_json') or '') then
        return {'', '', '', 'capabilities_changed'}
    end
    final_id, outcome = stored_id, 'renewed'
    final_granted_ms = stored_granted > 0 and stored_granted or now_ms
    final_sequence = stored_sequence > 0 and stored_sequence or 0
else
    final_id, outcome = new_lease_id, 'new'
    final_granted_ms = now_ms
    local current_sequence = tonumber(redis.call('GET', seq_key) or '0')
    if current_sequence < now_ms then redis.call('SET', seq_key, tostring(now_ms)) end
    final_sequence = redis.call('INCR', seq_key)
end
redis.call('HSET', lease_key, 'lease_id', final_id,
    'expires_at_ms', tostring(expires_at_ms),
    'granted_at_ms', tostring(final_granted_ms),
    'sequence', tostring(final_sequence), 'worker_id', worker_id)
if metrics_json ~= '' then
    redis.call('HSET', lease_key, 'metrics_json', metrics_json)
elseif outcome == 'new' then
    redis.call('HDEL', lease_key, 'metrics_json')
end
if capabilities_json ~= '' then
    redis.call('HSET', lease_key, 'capabilities_json', capabilities_json)
end
redis.call('PEXPIRE', lease_key, ttl_ms + record_retention_ms)
redis.call('ZADD', expiring_key, expires_at_ms, worker_id)
redis.call('SADD', active_key, worker_id)
return {final_id, tostring(expires_at_ms), tostring(final_granted_ms), outcome, tostring(final_sequence)}
"""

REVOKE_LUA = r"""
local lease_key, revoked_key = KEYS[1], KEYS[2]
local expiring_key, active_key = KEYS[3], KEYS[4]
local worker_id, expected_id = ARGV[1], ARGV[2]
local revoked_ttl = tonumber(ARGV[3])
-- 与 GRANT_LUA 一致：非法 TTL 直接报错。此前 clamp 成 1 秒会让撤销集在 1 秒后
-- 消失，lease_fenced_ready_publish 的 SISMEMBER(revoked_key, lease_id) 随即失效，
-- 已撤销的租约会重新变得可派发。
if revoked_ttl == nil or revoked_ttl < 1 then
    return redis.error_reply('lease revoked_ttl must be positive')
end
local existed = redis.call('EXISTS', lease_key)
local stored_id = redis.call('HGET', lease_key, 'lease_id')
if expected_id ~= '' and stored_id ~= expected_id then return 0 end
redis.call('DEL', lease_key)
redis.call('ZREM', expiring_key, worker_id)
redis.call('SREM', active_key, worker_id)
if stored_id and stored_id ~= '' then
    redis.call('SADD', revoked_key, stored_id)
    redis.call('EXPIRE', revoked_key, revoked_ttl)
end
return existed
"""

DISABLE_WORKER_LUA = r"""
local lease_key, revoked_key = KEYS[1], KEYS[2]
local expiring_key, active_key, lifecycle_key = KEYS[3], KEYS[4], KEYS[5]
local heartbeat_key = KEYS[6]
local worker_id, reason, revoked_ttl = ARGV[1], ARGV[2], tonumber(ARGV[3])
local existed = redis.call('EXISTS', lease_key)
local stored_id = redis.call('HGET', lease_key, 'lease_id')
redis.call('SET', lifecycle_key, reason)
redis.call('DEL', lease_key, heartbeat_key)
redis.call('ZREM', expiring_key, worker_id)
redis.call('SREM', active_key, worker_id)
if stored_id and stored_id ~= '' then
    redis.call('SADD', revoked_key, stored_id)
    redis.call('EXPIRE', revoked_key, revoked_ttl)
end
return existed
"""

ENABLE_WORKER_LUA = r"""
local lifecycle_key = KEYS[1]
if #ARGV > 0 then
    local stored_reason = redis.call('GET', lifecycle_key)
    if stored_reason == false or stored_reason == nil then return 0 end
    local matched = false
    for index = 1, #ARGV do
        if stored_reason == ARGV[index] then matched = true break end
    end
    if not matched then return -1 end
end
return redis.call('DEL', lifecycle_key)
"""

SWEEP_DELETE_LUA = r"""
local lease_key, expiring_key, active_key = KEYS[1], KEYS[2], KEYS[3]
local now_ms, worker_id = tonumber(ARGV[1]), ARGV[2]
local doomed_id = redis.call('HGET', lease_key, 'lease_id')
local raw = redis.call('HGET', lease_key, 'expires_at_ms')
if raw == false or raw == nil then
    redis.call('ZREM', expiring_key, worker_id)
    redis.call('SREM', active_key, worker_id)
    return {1, doomed_id or ''}
end
local stored_expires = tonumber(raw)
if stored_expires == nil or stored_expires > now_ms then return {0, ''} end
redis.call('DEL', lease_key)
redis.call('ZREM', expiring_key, worker_id)
redis.call('SREM', active_key, worker_id)
return {1, doomed_id or ''}
"""


def parse_sweep_delete_result(result: Any) -> tuple[int, str]:
    """解析 ``SWEEP_DELETE_LUA`` 的返回值；结构不合法直接抛错，不猜测语义。"""
    if not isinstance(result, (list, tuple)) or len(result) != SWEEP_DELETE_RESULT_ARITY:
        raise RuntimeError(f"Lease sweep Lua 返回结构非法: {result!r}")
    deleted_flag = int(result[0] or 0)
    if deleted_flag not in (0, 1):
        raise RuntimeError(f"Lease sweep Lua deleted 标志非法: {deleted_flag!r}")
    return deleted_flag, result[1].decode() if isinstance(result[1], bytes) else str(result[1] or "")


__all__ = [
    "DISABLE_WORKER_LUA",
    "ENABLE_WORKER_LUA",
    "GRANT_LUA",
    "REVOKE_LUA",
    "SWEEP_DELETE_LUA",
    "parse_sweep_delete_result",
]
