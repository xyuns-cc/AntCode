"""P1-DR-02 (round6) 回归:ACL 轮换加 SELECT FOR UPDATE 行锁防 Redis/PG 分裂。

审查文档 round6 5.1 P1-DR-02:
`Redis SETUSER 与 PG save 跨存储,无行锁/version CAS; 并发轮换可产生
Redis/PG 凭据分裂`。

Bug 场景:
- 两个并发 ensure_worker_acl
- 都读 revision=N (无锁)
- 都 Redis SETUSER (新密码 A/B, Redis 后写覆盖 = B)
- 都 PG save revision=N+1, password_encrypted=各自的 (PG 后写 = A)
- 最终 Redis=B / PG=A 分裂,Worker 用 PG 里的 A 认证 Redis 失败,永久失联

修复:事务内 SELECT FOR UPDATE 让两个调用串行:第 1 个拿锁 → Redis
SETUSER + PG save → 释放;第 2 个拿锁,从 PG 重读 revision=N+1,继续
SETUSER N+2,保持一致。

本测试锁死:
1. ensure_worker_acl 源码引用 in_transaction + select_for_update
2. 传入无 ORM id 的 fake worker 时降级 (测试路径),保持原逻辑
3. _save_acl_fields 支持 conn kwarg,让 save 走同一事务
"""

from __future__ import annotations

import inspect

from antcode_core.common.security import redis_acl


def test_ensure_worker_acl_source_uses_transaction_and_row_lock():
    """P1-DR-02: ensure_worker_acl 源码必须包含 in_transaction + select_for_update。"""
    src = inspect.getsource(redis_acl.ensure_worker_acl)
    assert "in_transaction" in src, "ensure_worker_acl 未走事务,并发轮换会分裂 Redis/PG"
    assert "select_for_update" in src, "ensure_worker_acl 未拿行锁,无法阻止并发"


def test_ensure_worker_acl_reads_fresh_row_within_lock():
    """P1-DR-02: 拿锁后必须重读 PG worker 行,避免用 stale revision 覆盖。"""
    src = inspect.getsource(redis_acl.ensure_worker_acl)
    assert "fresh" in src, "未重读 fresh worker,可能用 stale revision"
    assert "fresh.redis_acl_revision" in src or "worker.redis_acl_revision = fresh" in src


def test_save_acl_fields_accepts_conn_kwarg():
    """P1-DR-02: _save_acl_fields 必须支持 conn kwarg,让 save 走同一事务。"""
    sig = inspect.signature(redis_acl._save_acl_fields)
    assert "conn" in sig.parameters, "_save_acl_fields 缺 conn 参数,save 不会走事务"


def test_ensure_worker_acl_has_within_tx_helper():
    """P1-DR-02: 拆分 _ensure_worker_acl_within_tx helper,让锁与核心逻辑分离。"""
    assert hasattr(redis_acl, "_ensure_worker_acl_within_tx"), (
        "缺 _ensure_worker_acl_within_tx helper, 锁与核心逻辑未分离"
    )
