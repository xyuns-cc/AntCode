"""P1-GW-01 (round6 彻底修): grant 严格单调 sequence 消除同毫秒碰撞。

审查文档 round6 P1-GW-01:
`granted_at_ms 取整到毫秒,同一毫秒 L1/L2 可得到相同代际`。仅靠
granted_at_ms 作 gen 时,同毫秒 grant 无法区分,CAS `stored_gen <= new_gen`
允许覆盖。

修:grant Lua 用 Redis INCR 拿严格单调 sequence 作 tie-breaker。Gateway
bind 优先用 sequence 作 lease_gen;存量 lease(sequence=0)回退 granted_at_ms
保持向后兼容。

本测试锁死:
1. Lease dataclass 有 sequence 字段
2. Lua 源码在 new 分支调 `INCR seq_key`
3. Lua 源码在 renew 分支保留 stored_sequence
4. Lua HSET 存 sequence 字段
5. Gateway _bind_lease_generation 优先用 lease.sequence 作 gen
"""

from __future__ import annotations

import inspect

from antcode_core.application.services import lease_service
from antcode_core.application.services.lease_service import Lease


def test_lease_dataclass_has_sequence_field():
    """P1-GW-01: Lease 必须暴露 sequence 字段作为 gen tie-breaker。"""
    fields = {f.name for f in Lease.__dataclass_fields__.values()}
    assert "sequence" in fields, "Lease 缺 sequence 字段,同毫秒 grant 无法 tie-break"


def test_grant_lua_incr_seq_on_new_branch():
    """P1-GW-01: Lua new 分支必须 INCR seq_key 拿严格单调值。"""
    src = inspect.getsource(lease_service)
    assert "INCR', seq_key" in src, "Lua new 分支未 INCR seq_key,同毫秒 grant 会碰撞"
    assert "seq_key = KEYS[5]" in src, "Lua 未接受 KEYS[5]=seq_key"


def test_grant_lua_preserves_sequence_on_renew():
    """P1-GW-01: Lua renew 分支必须保留 stored_sequence,不能 INCR 前进。"""
    src = inspect.getsource(lease_service)
    assert "stored_sequence" in src, "Lua 未读 stored_sequence,renew 会前进"
    assert "final_sequence = stored_sequence > 0 and stored_sequence or 0" in src, (
        "Lua renew 分支未保留原 sequence"
    )


def test_grant_lua_hset_persists_sequence():
    """P1-GW-01: HSET 必须存 sequence 字段,get() 才能读到。"""
    src = inspect.getsource(lease_service)
    assert "'sequence', tostring(final_sequence)" in src, "HSET 未持久化 sequence"


def test_grant_returns_sequence():
    """P1-GW-01: Lua 返回值第 5 项是 sequence,grant() 解包到 Lease.sequence。"""
    src = inspect.getsource(lease_service)
    assert "tostring(final_sequence)" in src, "Lua 返回未包含 sequence"
    assert "sequence=final_sequence" in src, "grant() 未把 sequence 装进 Lease"


def test_seq_key_shares_slot_with_lease_keys():
    """P1-GW-01: seq_key 必须与 lease_key 同 Redis Cluster slot(hash tag {ns})。"""
    from antcode_core.application.services.lease_service import LeaseStore

    store = LeaseStore(object(), namespace="tenant-a")
    assert store._seq_key() == "{tenant-a}:lease:sequence"
    assert store._seq_key().startswith("{tenant-a}"), (
        "seq_key 不与 lease_key 同 slot,Cluster 模式下 Lua INCR 会跨槽失败"
    )


def test_gateway_bind_prefers_sequence_over_granted_at_ms():
    """P1-GW-01: Gateway _bind_lease_generation 优先 lease.sequence 作 gen。"""
    from antcode_gateway.services import run_ownership_rpc

    src = inspect.getsource(run_ownership_rpc)
    # 必须包含"sequence 优先,granted_at_ms 回退"逻辑
    assert "lease.sequence" in src, "bind 未用 lease.sequence"
    assert "lease.granted_at_ms" in src, "bind 未保留 granted_at_ms 兼容回退"


def test_lease_default_sequence_is_zero_for_backward_compat():
    """P1-GW-01: 存量 Lease Hash 无 sequence 字段时 fallback 0,不破坏兼容。"""
    lease = Lease(
        worker_id="w-1",
        lease_id="lease-1",
        expires_at_ms=1000,
        granted_at_ms=500,
    )
    assert lease.sequence == 0, "Lease.sequence 默认应为 0(向后兼容 fallback)"
