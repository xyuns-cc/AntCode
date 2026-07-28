"""run ownership fence 的 Lua 脚本本体（与 ``run_ownership_fence`` 同契约）。

拆出脚本文本以保持 fence 模块在 300 行红线内；语义注释与 ARGV/KEYS
布局说明保留在脚本旁。
"""

from __future__ import annotations

# KEYS[1] = ownership key, KEYS[2] = lease key
# ARGV[1] = token(worker:lease), ARGV[2] = ttl_ms, ARGV[3] = worker_id,
# ARGV[4] = lease_id, ARGV[5] = lease record retention ms
#
# 语义：
# 1. 权威 Lease Hash 必须仍持有 ARGV[4] 且 PTTL 未进入 retention 窗口
#    （与 ``LeaseStore.is_current`` 完全一致），否则拒绝（-1）。
# 2. 空 key / 自己 token → 直接取得。
# 3. 同 worker 的旧代际 token → 接管：第 1 步已证明请求方是该 worker 的
#    唯一现行代际，旧 token 是死代际残留，不应阻塞最长 65 分钟。
# 4. 其他 worker 的 token → 拒绝（0），跨 Worker 互斥交给 TTL 到期。
_CLAIM_SCRIPT = """
local stored_lease = redis.call('HGET', KEYS[2], 'lease_id')
if stored_lease ~= ARGV[4] then
    return -1
end
local lease_pttl = redis.call('PTTL', KEYS[2])
if (not lease_pttl) or lease_pttl <= tonumber(ARGV[5]) then
    return -1
end
local current = redis.call('GET', KEYS[1])
if (not current) or current == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
    return 1
end
local prefix = ARGV[3] .. ':'
if string.sub(current, 1, string.len(prefix)) == prefix then
    redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
    return 1
end
return 0
"""

_RENEW_SCRIPT = """
local stored_lease = redis.call('HGET', KEYS[2], 'lease_id')
if stored_lease ~= ARGV[4] then
    return -1
end
local lease_pttl = redis.call('PTTL', KEYS[2])
if (not lease_pttl) or lease_pttl <= tonumber(ARGV[5]) then
    return -1
end
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
    return 1
end
return 0
"""

# 复审 P1-DR-02: 跨 Worker 接管。此前"其他 worker 的 token → 一律等
# TTL 到期"意味着节点崩溃后同 run 最长停滞 65 分钟。本脚本在 claim 的
# 基础上增加死主判定：当前 holder 的 token 与调用方读到的一致（防
# GET→EVAL 窗口内换主），且 holder 的权威 Lease Hash 已不再持有 token
# 中的代际（换代或过期），才允许接管。holder lease key 与 owner key 同
# ``{ns}`` hash tag（同 slot），Cluster 下合法。
#
# KEYS[1]=owner key, KEYS[2]=claimant lease key, KEYS[3]=holder lease key
# ARGV[1]=token, ARGV[2]=ttl_ms, ARGV[3]=worker_id, ARGV[4]=lease_id,
# ARGV[5]=retention_ms, ARGV[6]=expected holder token, ARGV[7]=holder lease_id
_TAKEOVER_SCRIPT = """
local stored_lease = redis.call('HGET', KEYS[2], 'lease_id')
if stored_lease ~= ARGV[4] then
    return -1
end
local lease_pttl = redis.call('PTTL', KEYS[2])
if (not lease_pttl) or lease_pttl <= tonumber(ARGV[5]) then
    return -1
end
local current = redis.call('GET', KEYS[1])
if (not current) or current == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
    return 1
end
if current ~= ARGV[6] then
    return 0
end
local holder_lease = redis.call('HGET', KEYS[3], 'lease_id')
if holder_lease == ARGV[7] then
    local holder_pttl = redis.call('PTTL', KEYS[3])
    if holder_pttl and holder_pttl > tonumber(ARGV[5]) then
        return 0
    end
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
return 1
"""

# release 只要求 token 精确匹配：旧代际释放自己的残留 token 是安全且
# 有益的（提前解锁新代际），不需要 lease 校验。
_RELEASE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
    return 1
end
if current == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""

__all__ = ["_CLAIM_SCRIPT", "_RELEASE_SCRIPT", "_RENEW_SCRIPT", "_TAKEOVER_SCRIPT"]
