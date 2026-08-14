"""Direct Worker Redis ACL least-privilege policy contracts."""

from __future__ import annotations

from fnmatch import fnmatchcase

import pytest


def _selectors_for(pattern: str):
    from antcode_core.common.security.redis_acl import ACL_SELECTOR_SPECS

    return [(set(commands), access) for commands, access, patterns in ACL_SELECTOR_SPECS if pattern in patterns]


def test_acl_policy_uses_only_explicit_commands():
    from antcode_core.common.security.redis_acl import ACL_BASE_COMMAND_RULES, ACL_SELECTOR_SPECS

    all_commands = [*ACL_BASE_COMMAND_RULES]
    all_commands.extend(command for commands, _access, _patterns in ACL_SELECTOR_SPECS for command in commands)

    assert all(not command.startswith("+@") for command in all_commands)
    assert "+script|load" not in ACL_BASE_COMMAND_RULES
    assert {"+multi", "+exec", "+discard"}.issubset(ACL_BASE_COMMAND_RULES)


def test_acl_policy_rejects_worker_id_glob_injection():
    from antcode_core.common.security.redis_acl import _build_setuser_args

    with pytest.raises(ValueError, match="worker_id"):
        _build_setuser_args("worker_bad", "secret", "bad*", namespace="antcode")


def test_runtime_control_keys_match_only_the_own_worker_acl_patterns():
    from antcode_core.common.security.redis_acl import ACL_SELECTOR_SPECS
    from antcode_core.infrastructure.redis import (
        control_reply_stream,
        runtime_control_request_id,
        runtime_control_settlement_key,
    )

    worker_id = "worker-1"
    request_id = runtime_control_request_id(worker_id, "a" * 32)
    patterns = [
        pattern for _commands, _access, selector_patterns in ACL_SELECTOR_SPECS for pattern in selector_patterns
    ]
    own_patterns = [pattern.format(ns="antcode", wid=worker_id) for pattern in patterns if "{wid}" in pattern]
    foreign_patterns = [pattern.format(ns="antcode", wid="worker-2") for pattern in patterns if "{wid}" in pattern]
    actual_keys = (
        control_reply_stream(request_id),
        runtime_control_settlement_key(worker_id, "antcode:control:worker-1|1-0|worker-1"),
    )

    assert all(any(fnmatchcase(key, pattern) for pattern in own_patterns) for key in actual_keys)
    assert all(not any(fnmatchcase(key, pattern) for pattern in foreign_patterns) for key in actual_keys)


def test_result_ingest_stream_is_xadd_only():
    assert _selectors_for("{ns}:task:result") == [({"+xadd"}, "%W")]


def test_log_ingest_requires_trusted_control_plane():
    assert _selectors_for("{ns}:log:ingest") == []


def test_task_and_control_streams_keep_only_required_pel_commands():
    task_commands, task_access = _selectors_for("{{{ns}}}:task:ready:{wid}")[0]
    control_commands, control_access = _selectors_for("{{{ns}}}:control:{wid}")[0]

    assert task_access == control_access == "%RW"
    assert task_commands == {
        "+xgroup|create",
        "+xreadgroup",
        "+xpending",
        "+xclaim",
        "+xack",
        "+xadd",
        "+xinfo|groups",
        "+xtrim",
        "+xrange",
    }
    assert control_commands == {
        "+xgroup|create",
        "+xreadgroup",
        "+xpending",
        "+xclaim",
        "+xack",
        "+xinfo|groups",
        "+xtrim",
        "+xrange",
        "+eval",
    }
    assert _selectors_for("{ns}:control:global") == []


def test_task_requeue_lua_is_scoped_to_source_and_own_marker():
    source_pattern = "{{{ns}}}:task:ready:{wid}"
    marker_pattern = "{{{ns}}}:task:ready:{wid}:requeue:*"

    assert ({"+eval"}, "%RW") in _selectors_for(source_pattern)
    assert _selectors_for(marker_pattern) == [
        ({"+get", "+set", "+expireat"}, "%RW"),
        ({"+eval"}, "%RW"),
    ]
    assert _selectors_for("{{{ns}}}:task:ready:{wid}:ack:*") == [
        ({"+get", "+set", "+expire"}, "%RW"),
        ({"+eval"}, "%RW"),
    ]


def test_worker_lease_permissions_are_read_only():
    assert _selectors_for("{{{ns}}}:lease:data:{wid}") == [
        ({"+eval"}, "%RW"),
        ({"+hget", "+exists", "+pttl"}, "%R"),
    ]
    assert _selectors_for("{{{ns}}}:lease:revoked:{wid}") == [({"+sismember"}, "%R")]
    assert _selectors_for("{{{ns}}}:lease:lifecycle:{wid}") == [({"+exists"}, "%R")]
    assert _selectors_for("{{{ns}}}:lease:expiring") == []
    assert _selectors_for("{{{ns}}}:lease:active") == []


def test_runtime_settlement_lua_is_scoped_to_marker_and_lease():
    marker_pattern = "{{{ns}}}:control:settlement:{wid}:*"
    assert _selectors_for(marker_pattern) == [
        ({"+set", "+get", "+pexpireat"}, "%RW"),
        ({"+eval"}, "%RW"),
    ]
    lease_selectors = _selectors_for("{{{ns}}}:lease:data:{wid}")
    assert ({"+eval"}, "%RW") in lease_selectors
    assert any({"+hget", "+pttl"}.issubset(commands) for commands, _access in lease_selectors)
    assert all("+pexpire" not in commands for commands, _access in lease_selectors)


def test_spider_and_run_permissions_match_direct_write_paths():
    from antcode_core.common.security.redis_acl import ACL_SELECTOR_SPECS

    assert _selectors_for("{{{ns}}}:spider:index:*") == []
    assert _selectors_for("{{{ns}}}:spider:*:item-ids") == []
    assert _selectors_for("{{{ns}}}:spider:*:data") == []
    assert _selectors_for("{{{ns}}}:spider:*:meta") == []
    assert _selectors_for("{{{ns}}}:spider:*:tombstone") == []
    assert _selectors_for("{{{ns}}}:run:owner:*") == []


def test_foreign_lease_access_is_read_only():
    """P1-DR-02: 对任意 worker lease Hash 的宽 pattern 只允许只读判活。

    takeover Lua 需要 HGET lease_id / PTTL 判定 holder 是否已死；除
    +eval（声明 key 用）外不得出现任何写命令 —— 写权限只允许出现在
    {wid} 自有 key 的选择器上。
    """
    wide_selectors = _selectors_for("{{{ns}}}:lease:data:*")

    assert wide_selectors == []


def test_unused_shared_worker_surfaces_are_not_authorized():
    from antcode_core.common.security.redis_acl import ACL_SELECTOR_SPECS

    joined = " ".join(pattern for _commands, _access, patterns in ACL_SELECTOR_SPECS for pattern in patterns)
    for forbidden in (
        "log:stream:*",
        "log:chunk:*",
        "task:dead_letter",
        "worker:all",
        "heartbeat:active",
        "spider:config:*",
        "item-ids",
        "item-order",
    ):
        assert forbidden not in joined


# P1-SEC-02 复核冻结的共享面 allowlist：这些 pattern 不含 {wid} 维度，
# 是 ACL 表达力无法进一步收紧的残余风险面（详见 redis_acl.py 模块
# docstring）。新增任何共享 pattern 或对现有共享面扩权都必须先过这里。
_SHARED_PATTERN_ALLOWLIST = frozenset(
    {
        "{ns}:task:result",
    }
)


def test_shared_surface_allowlist_is_frozen():
    """无 {wid} 维度的 pattern 集合必须与已审计的残余风险面完全一致。"""
    from antcode_core.common.security.redis_acl import ACL_SELECTOR_SPECS

    shared_patterns = {
        pattern for _commands, _access, patterns in ACL_SELECTOR_SPECS for pattern in patterns if "{wid}" not in pattern
    }

    assert shared_patterns == _SHARED_PATTERN_ALLOWLIST


def test_trusted_control_plane_keys_are_not_authorized():
    from antcode_core.common.security.redis_acl import ACL_BASE_COMMAND_RULES, ACL_SELECTOR_SPECS

    patterns = {pattern for _commands, _access, values in ACL_SELECTOR_SPECS for pattern in values}
    assert "{{{ns}}}:lease:sequence" not in patterns
    assert "{{{ns}}}:lease:expiring" not in patterns
    assert "{{{ns}}}:lease:active" not in patterns
    assert "{{{ns}}}:run:owner:*" not in patterns
    assert "{{{ns}}}:lease:data:*" not in patterns
    assert "+incr" not in ACL_BASE_COMMAND_RULES


def test_heartbeat_selector_covers_every_command_the_worker_issues():
    """心跳写回路径用到的命令必须全部在 ACL 里。

    真实环境事故：selector 少了 ``+hdel``，而 worker 的
    ``write_legacy_heartbeat_hash`` 会在本次心跳不含 spider_stats 时 HDEL 掉旧值。
    整条 pipeline 被 NoPermissionError 打断 → 心跳连续失败 5 次触发重连，
    Worker 无法维持 online。
    """
    from antcode_core.common.security.redis_acl import ACL_SELECTOR_SPECS

    heartbeat_commands: set[str] = set()
    for commands, _access, patterns in ACL_SELECTOR_SPECS:
        if any("heartbeat:{wid}" in pattern for pattern in patterns):
            heartbeat_commands.update(commands)

    required = {"+hset", "+hdel", "+expire"}
    missing = required - heartbeat_commands
    assert not missing, f"Worker 心跳会发出但 ACL 未授权的命令: {sorted(missing)}"


_CANCEL_TOMBSTONE_PATTERN = "{{{ns}}}:cancel:tombstone:{wid}:*"


class _RecordingRedis:
    """记录 tombstone 真实发出的 (ACL 命令名, key)，用于与授权集绑死。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.calls.append(("+set", key))

    async def delete(self, key: str) -> None:
        self.calls.append(("+del", key))

    async def getdel(self, key: str) -> None:
        self.calls.append(("+getdel", key))
        return None


@pytest.mark.asyncio
async def test_cancel_tombstone_selector_covers_every_command_the_worker_issues():
    """取消墓碑真实发出的命令与 key 必须完全落在自己那条 selector 里。

    真实环境事故：键是无 {wid} 维度的 ``{ns}:cancel:tombstone:<run_id>``，
    无法在最小权限 ACL 下授权，Worker 侧报
    ``has no permissions to run the 'getdel' command``，跨进程取消 fence
    静默失效。本测试把「实际发出的命令」与「ACL 授权的命令」绑死，
    并锁死 key 只落在本 Worker 的 pattern 上。
    """
    from antcode_worker.engine.cancel_tombstones import CancelTombstones

    namespace, worker_id = "antcode", "worker-1"
    redis = _RecordingRedis()
    tombstones = CancelTombstones(redis_client=redis, namespace=namespace, worker_id=worker_id)

    await tombstones.record("run-1", reason="user-cancel")  # SET
    await tombstones.consume("run-1")  # 内存命中 → DEL 清备份
    await tombstones.consume("run-2")  # 内存 miss → GETDEL

    selectors = _selectors_for(_CANCEL_TOMBSTONE_PATTERN)
    granted = {command for commands, _access in selectors for command in commands}
    issued = {command for command, _key in redis.calls}

    assert issued, "tombstone 未发出任何 Redis 命令，契约测试失去意义"
    assert issued <= granted, f"tombstone 会发出但 ACL 未授权的命令: {sorted(issued - granted)}"
    # GETDEL 同时需要读写权限，selector 必须是 %RW。
    assert all(access == "%RW" for _commands, access in selectors)


def test_cancel_tombstone_keys_never_reach_another_worker():
    """墓碑 key 必须只匹配自身 {wid} pattern，不得落进其他 Worker 的面。"""
    from antcode_core.infrastructure.redis import cancel_tombstone_key

    namespace = "antcode"
    own = _CANCEL_TOMBSTONE_PATTERN.format(ns=namespace, wid="worker-1")
    foreign = _CANCEL_TOMBSTONE_PATTERN.format(ns=namespace, wid="worker-2")
    key = cancel_tombstone_key("worker-1", "run-1", namespace=namespace)

    assert fnmatchcase(key, own)
    assert not fnmatchcase(key, foreign)
    assert not fnmatchcase(cancel_tombstone_key("worker-2", "run-1", namespace=namespace), own)
