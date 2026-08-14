"""Direct Worker Redis ACL 策略表（最小权限 selector 声明）。

从 ``redis_acl.py`` 拆出的纯数据模块：每个 selector 是一条独立的
(commands, access, key patterns) 能力。共享面（无 {wid} 维度的
pattern）的残余风险以 ``redis_acl.py`` 模块 docstring 为准（P1-SEC-02
复核结论）；对应的策略契约测试见
``tests/unit/core/test_redis_acl_policy.py``。
"""

from __future__ import annotations

ACL_BASE_COMMAND_RULES = (
    "+ping",
    "+time",
    "+select",
    "+multi",
    "+exec",
    "+discard",
)

# Each selector is an independent least-privilege (commands, access, key patterns) capability.
ACL_SELECTOR_SPECS: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (("+set",), "%W", ("{ns}:direct:register:{wid}",)),
    (
        (
            "+xgroup|create",
            "+xreadgroup",
            "+xpending",
            "+xclaim",
            "+xack",
            "+xadd",
            "+xinfo|groups",
            "+xtrim",
            "+xrange",
        ),
        "%RW",
        ("{{{ns}}}:task:ready:{wid}",),
    ),
    (("+get", "+set", "+expireat"), "%RW", ("{{{ns}}}:task:ready:{wid}:requeue:*",)),
    (("+get", "+set", "+expire"), "%RW", ("{{{ns}}}:task:ready:{wid}:ack:*",)),
    (
        ("+eval",),
        "%RW",
        (
            "{{{ns}}}:task:ready:{wid}",
            "{{{ns}}}:task:ready:{wid}:requeue:*",
            "{{{ns}}}:task:ready:{wid}:ack:*",
        ),
    ),
    (
        ("+eval", "+get", "+xrange", "+xadd", "+set", "+expire"),
        "%RW",
        ("{{{ns}}}:task:ready:{wid}:{{dlq}}:dead_letter*",),
    ),
    (
        (
            "+xgroup|create",
            "+xreadgroup",
            "+xpending",
            "+xclaim",
            "+xack",
            "+xinfo|groups",
            "+xtrim",
            "+xrange",
            "+eval",
        ),
        "%RW",
        ("{{{ns}}}:control:{wid}",),
    ),
    (("+xadd", "+xrange", "+pexpireat"), "%RW", ("{ns}:control:reply:{wid}:*",)),
    (("+set", "+get", "+pexpireat"), "%RW", ("{{{ns}}}:control:settlement:{wid}:*",)),
    (
        ("+eval",),
        "%RW",
        ("{{{ns}}}:control:settlement:{wid}:*", "{{{ns}}}:lease:data:{wid}"),
    ),
    # +hdel 用于清掉本次心跳不再上报的可选字段（如 spider_stats），见
    # worker ``transport/redis/heartbeat_hash.py::write_legacy_heartbeat_hash``。
    # 缺它会让每次不带 spider_stats 的心跳整条 pipeline 被 NoPermissionError 打断
    # → 连续 5 次失败触发重连（真实环境实测）。权限面没有扩大：同一个 key 上
    # 已经授予了破坏力更强的 +del（整键删除）。
    (("+hset", "+hdel", "+expire", "+del"), "%RW", ("{{{ns}}}:heartbeat:{wid}",)),
    # 取消墓碑：cancel 跑赢 task 时的本地 fence，重启后靠 Redis 幸存。
    # ``+getdel`` 是消费端的原子取值+删（保证只被消费一次），``+set`` 写入
    # 带 TTL 的墓碑，``+del`` 用于内存已命中时清理备份。key pattern 必须带
    # {wid}：共享 ``cancel:tombstone:*`` 会让任一 Worker GETDEL 掉别人的
    # fence，被取消的 run 反而照常执行。
    (("+set", "+getdel", "+del"), "%RW", ("{{{ns}}}:cancel:tombstone:{wid}:*",)),
    # Lease 的签发、续租、撤销和索引维护全部属于可信控制面。Direct Worker
    # 只读自身代际，用于本地 generation guard；不能获得任何权威 Lease 写权。
    (("+hget", "+exists", "+pttl"), "%R", ("{{{ns}}}:lease:data:{wid}",)),
    (("+sismember",), "%R", ("{{{ns}}}:lease:revoked:{wid}",)),
    (("+exists",), "%R", ("{{{ns}}}:lease:lifecycle:{wid}",)),
    (("+xadd",), "%W", ("{ns}:task:result",)),
    # 广播由可信控制面 fan-out 到各 Worker 的 control:{wid}。Redis ACL
    # 无法约束 consumer-group 参数，因此 Direct Worker 不得访问共享
    # control:global，否则可读取/claim/ACK 其他 Worker 的广播或创建孤儿组。
    # SpiderData 由 HMAC/API Key 认证的 Web API 控制面代写。ACL 无法表达
    # “只写当前 Worker 持有 ownership 的 run”，因此 Direct Worker 不得
    # 获得任何 spider data/meta/index/dedup 底层权限。
    # Run ownership 的 claim/renew/release 同样属于可信控制面。Redis ACL
    # 无法限制裸 DEL/PEXPIRE 只能操作调用方持有的 token，因此 Worker 对
    # ownership key 不保留任何直接权限。
)

__all__ = [
    "ACL_BASE_COMMAND_RULES",
    "ACL_SELECTOR_SPECS",
]
