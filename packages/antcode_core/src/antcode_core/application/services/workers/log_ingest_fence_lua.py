"""Atomic Lease and ownership fence for log-ingest XADD."""

_APPEND_LOG_BATCH_SCRIPT = r"""
local lease_key = KEYS[1]
local stream_key = KEYS[2]
local worker_id = ARGV[1]
local lease_id = ARGV[2]
local token = ARGV[3]
local retention_ms = tonumber(ARGV[4])
local payload = ARGV[5]

if redis.call('HGET', lease_key, 'lease_id') ~= lease_id then
    return {-1, 'lease_stale'}
end
local lease_pttl = redis.call('PTTL', lease_key)
if (not lease_pttl) or lease_pttl <= retention_ms then
    return {-1, 'lease_stale'}
end
for index = 3, #KEYS do
    if redis.call('GET', KEYS[index]) ~= token then
        return {0, 'run_not_owned'}
    end
end
local message_id = redis.call('XADD', stream_key, '*', 'p', payload)
return {1, message_id}
"""

__all__ = ["_APPEND_LOG_BATCH_SCRIPT"]
