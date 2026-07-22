"""P1-GW-04 (round4 修正) 回归:lease_gen 用 granted_at_ms 而非 time.time()。

round4 审查文档 code-review-2026-07-22-round4-review.md P1-GW-01:
批次 2 我在 _bind_lease_generation 里用 int(time.time() * 1000) 作为 gen。
但这是 fence 之后 bind 时刻,不是 lease 授予时刻。攻击场景:
- L1 fence 在 T=110 → asyncio 调度切走
- L2 fence 在 T=210 → bind 用 time.time()=210, PG.lease_gen=210
- L1 在 T=250 恢复 → bind 用 time.time()=250, CAS `210 <= 250`? true
  → 覆盖 L2, 反而是旧 L1 生效

修法:用 lease 的 granted_at_ms 作 gen(lease 授予时刻,L1 的 lease 授予
早于 L2, granted_at_ms 更小)。同场景:
- L1 gen = 100 (Lease-1 授予时刻)
- L2 gen = 200 (Lease-2 授予时刻)
- L1 迟到 bind: NEW=100, PG=200, CAS `200 <= 100`? false → 拒 ✓

本测试锁死:
1. bind 时 lease_gen 与 Lease Hash.granted_at_ms 一致(而非 time.time())
2. gen 值稳定不随时间变(相同 lease 多次 claim 应得同一 gen)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.unit.gateway.test_run_ownership_rpc import (
    _claim,
    _context,
    _install,
    _Redis,
    _Service,
    fence,
    module,
)

_GRANTED_AT_MS_DISTINCT = 555  # 与 _install() 默认 100 明显不同,验证读取的是 lease 上的实际值
_GRANTED_AT_MS_LOOP = 42  # 循环调用共享的固定值,反证不是 time.time()


@pytest.mark.asyncio
async def test_bind_gen_equals_lease_granted_at_ms(monkeypatch):
    """P1-GW-04 (round4):lease_gen 必须等于 Lease Hash.granted_at_ms。"""
    redis = _Redis()
    bind_generation, *_ = _install(monkeypatch, redis)

    # 显式设置 granted_at_ms 覆盖 _install 默认的 100
    lease_key = fence._lease_key("worker-1", None)
    redis.lease_hash[lease_key] = {
        "lease_id": "lease-1",
        "granted_at_ms": str(_GRANTED_AT_MS_DISTINCT),
        "expires_at_ms": "999999999",
    }

    response = await _Service().ClaimRunOwnership(_claim(), _context())

    assert response.acquired is True
    call = bind_generation.await_args
    assert call.kwargs["lease_gen"] == _GRANTED_AT_MS_DISTINCT


@pytest.mark.asyncio
async def test_bind_gen_stable_across_multiple_claims(monkeypatch):
    """P1-GW-04 (round4):同一 lease 多次 claim,gen 稳定不变(反证不是 time.time())。"""
    redis = _Redis()
    bind_generation, *_ = _install(monkeypatch, redis)
    lease_key = fence._lease_key("worker-1", None)
    redis.lease_hash[lease_key] = {
        "lease_id": "lease-1",
        "granted_at_ms": str(_GRANTED_AT_MS_LOOP),
        "expires_at_ms": "999999999",
    }
    claim_rounds = 3

    for _ in range(claim_rounds):
        await _Service().ClaimRunOwnership(_claim(), _context())

    # 每次 claim 都读同一 granted_at_ms, 若旧 bug 用 time.time() 会每次不同
    assert bind_generation.await_count == claim_rounds
    for call in bind_generation.await_args_list:
        assert call.kwargs["lease_gen"] == _GRANTED_AT_MS_LOOP


@pytest.mark.asyncio
async def test_bind_aborts_when_lease_disappeared_between_fence_and_bind(monkeypatch):
    """P1-GW-04 (round4):fence ACQUIRED 后到 bind 前 lease 被撤销/换代 → 拒绝 bind。"""
    import grpc

    redis = _Redis()
    bind_generation, *_ = _install(monkeypatch, redis)
    lease_key = fence._lease_key("worker-1", None)
    # 显式清空 lease_hash,让 LeaseStore.get() 返回 None(hgetall 返回 {})
    redis.lease_hash.pop(lease_key, None)
    redis.lease[lease_key] = ""  # store.get() 从 hgetall 判 empty → None

    context = _context()
    response = await _Service().ClaimRunOwnership(_claim(), context)

    assert response.acquired is False
    # abort 应用 FAILED_PRECONDITION 而不是 UNAVAILABLE/PERMISSION_DENIED
    assert context.abort.await_args.args[0] == grpc.StatusCode.FAILED_PRECONDITION
    bind_generation.assert_not_awaited()
