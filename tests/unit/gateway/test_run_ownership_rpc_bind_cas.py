"""Gateway run ownership RPC — PG bind CAS 与错误路径测试。

P0-03a: 从 test_run_ownership_rpc.py 拆出的 P1-GW-04 / bind 边界用例,让原
文件保持在 300 行内。共用 fixture (_Redis/_Service/_context/_claim/_install)
从原文件 import 复用。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import grpc
import pytest

from tests.unit.gateway.test_run_ownership_rpc import (
    MAX_RUN_OWNERSHIP_TTL_MS,
    _claim,
    _context,
    _install,
    _Redis,
    _Service,
    fence,
)


@pytest.mark.asyncio
async def test_claim_binds_pg_generation_only_after_fence_acquired(monkeypatch):
    """P1-GW-02/04: fence ACQUIRED → PG bind 带 lease_gen 单调 CAS。"""
    redis = _Redis()
    bind_generation, _owns, _owns_lease = _install(monkeypatch, redis)

    response = await _Service().ClaimRunOwnership(_claim(), _context())

    assert response.acquired is True
    assert bind_generation.await_count == 1
    call = bind_generation.await_args
    assert call.args == ("worker-1", "run-1")
    assert call.kwargs["lease_id"] == "lease-1"
    # P1-GW-04 (round4 修正): lease_gen 必须来自 Lease Hash 的 granted_at_ms
    # (授予时刻),不是 time.time() (bind 时刻)。_install() mock 里 granted_at_ms=100。
    # 反证:若旧 bug 存在(time.time()*1000),该值应远大于 100 (毫秒时间戳)。
    expected_gen_from_granted_at_ms = 100
    assert call.kwargs["lease_gen"] == expected_gen_from_granted_at_ms
    assert call.kwargs["log_cutoff_id"] == "10-0"


@pytest.mark.asyncio
async def test_claim_final_fence_stale_releases_token_and_never_returns_acquired(monkeypatch):
    """PG bind 后 Lease 换代时，最终 fence 必须失败并精确释放旧 token。"""
    redis = _Redis()
    bind_generation, _owns, _owns_lease = _install(monkeypatch, redis)
    redis.renew_result = -1
    context = _context()

    response = await _Service().ClaimRunOwnership(_claim(), context)

    assert response.acquired is False
    bind_generation.assert_awaited_once()
    assert context.abort.await_args.args[0] == grpc.StatusCode.FAILED_PRECONDITION
    assert redis.values == {}


@pytest.mark.asyncio
async def test_claim_held_by_other_does_not_bind_pg(monkeypatch):
    """未取得 ownership 就绝不改绑 PG(另一存活 worker 正持有该 run)。"""
    redis = _Redis()
    bind_generation, _owns, _owns_lease = _install(monkeypatch, redis)
    redis.values["{tenant-a}:run:owner:run-1"] = "worker-2:lease-x"
    redis.lease[fence._lease_key("worker-2", None)] = "lease-x"

    response = await _Service().ClaimRunOwnership(_claim(), _context())

    assert response.acquired is False
    bind_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_bind_failure_after_fence_aborts_permission_denied(monkeypatch):
    """fence 后 run 已不属于该 worker(并发改派): abort 且不返回 acquired。"""
    redis = _Redis()
    bind_generation, _owns, _owns_lease = _install(monkeypatch, redis)
    bind_generation.side_effect = PermissionError("TaskRun 不存在或不属于当前 Worker")
    context = _context()
    context.abort.side_effect = RuntimeError("grpc aborted")

    with pytest.raises(RuntimeError, match="grpc aborted"):
        await _Service().ClaimRunOwnership(_claim(), context)

    assert context.abort.await_args.args[0] == grpc.StatusCode.PERMISSION_DENIED
    assert redis.values == {}


@pytest.mark.asyncio
async def test_invalid_ttl_is_rejected_before_lease_and_redis(monkeypatch):
    """超上限的 ttl_ms 参数校验先失败,不走 lease/redis。"""
    redis = _Redis()
    _install(monkeypatch, redis)
    service = _Service()
    context = _context()

    response = await service.ClaimRunOwnership(_claim(ttl_ms=MAX_RUN_OWNERSHIP_TTL_MS + 1), context)

    assert response.acquired is False
    assert context.abort.await_args.args[0] == grpc.StatusCode.INVALID_ARGUMENT
    service._lease_verifier.assert_not_awaited()
    assert redis.eval_calls == []
