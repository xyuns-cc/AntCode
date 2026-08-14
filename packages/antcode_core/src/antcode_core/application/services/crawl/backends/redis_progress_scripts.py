"""Atomic Redis scripts for Crawl progress and cancellation fences."""

REPLACE_ACTIVE_HASH = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('DEL', KEYS[1])
if #ARGV > 0 then redis.call('HSET', KEYS[1], unpack(ARGV, 1)) end
return 1
"""

UPDATE_ACTIVE_HASH = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
if #ARGV > 0 then redis.call('HSET', KEYS[1], unpack(ARGV, 1)) end
return 1
"""

INCREMENT_ACTIVE_HASH = """
if redis.call('EXISTS', KEYS[2]) == 1 then return {0, 0} end
local current = tonumber(redis.call('HGET', KEYS[1], ARGV[1]) or '0')
local value = current + tonumber(ARGV[2])
redis.call('HSET', KEYS[1], ARGV[1], tostring(value))
return {1, value}
"""

REGISTER_WORKER = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
redis.call('ZADD', KEYS[1], now_ms + tonumber(ARGV[2]), ARGV[1])
return 1
"""

LIST_ACTIVE_WORKERS = """
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
return redis.call('ZRANGEBYSCORE', KEYS[1], '(' .. now_ms, '+inf')
"""

FENCE_AND_CLEAR = """
redis.call('SET', KEYS[4], '1')
return redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
"""
