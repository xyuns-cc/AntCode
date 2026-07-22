from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import artifact_pb2
from antcode_core.application.services.workers import run_ownership_fence as fence
from antcode_gateway.services import run_ownership_rpc as module
from antcode_gateway.services.run_ownership_rpc import (
    MAX_RUN_OWNERSHIP_TTL_MS,
    RunOwnershipRpcMixin,
)

_LEASE_PTTL_MS = 60_000
_TAKEOVER_NUMKEYS = 3  # takeover Lua 额外携带 holder 的 lease key


class _Redis:
    """模拟 fence Lua 协议的最小 Redis。

    Lua 语义本体（lease 校验 + 同 worker 接管）由
    tests/unit/core/test_run_ownership_fence.py 按脚本合同覆盖；这里
    验证 RPC 粘合层：入参顺序、结果映射与 abort 行为。
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        # lease: 存 str (lease_id) 为原有 Lua 分支消费；同时用 lease_hash 存
        # {'lease_id':..., 'granted_at_ms':..., 'expires_at_ms':...} 供
        # LeaseStore.get() (P1-GW-04 修正后 bind 从 granted_at_ms 取 gen) 用。
        self.lease: dict[str, str] = {}
        self.lease_hash: dict[str, dict[str, str]] = {}
        self.pttl_ms = _LEASE_PTTL_MS
        self.eval_calls: list[tuple] = []

    async def get(self, key):
        return self.values.get(key)

    async def hgetall(self, key):
        # P1-GW-04: _bind_lease_generation 通过 LeaseStore.get() 拿
        # granted_at_ms 作为单调 gen。Mock 提供最小 Hash。
        stored_lease_id = self.lease.get(key)
        if not stored_lease_id:
            return {}
        # 若 lease_hash 有精细字段就用,否则合成合理默认(granted_at_ms=1)
        return self.lease_hash.get(
            key,
            {"lease_id": stored_lease_id, "granted_at_ms": "1", "expires_at_ms": "999999999"},
        )

    async def time(self):
        # LeaseStore.get(include_expired=False) 会调 redis.time();这里返回
        # 一个远小于 expires_at_ms 的固定值,保证 lease 不过期
        return (0, 0)

    async def eval(self, script, numkeys, *rest):
        keys, argv = rest[:numkeys], rest[numkeys:]
        self.eval_calls.append((script, keys, argv))
        if numkeys == 1:
            return self._eval_release(keys[0], argv[0])
        if self.lease.get(keys[1]) != argv[3] or self.pttl_ms <= int(argv[4]):
            return -1  # 权威 lease 校验失败（claim/renew/takeover 共同前置）
        if "PEXPIRE" in script:
            return self._eval_renew(keys[0], argv[0])
        if numkeys == _TAKEOVER_NUMKEYS:
            return self._eval_takeover(keys, argv)
        return self._eval_claim(keys[0], token=argv[0], worker_id=argv[2])

    def _eval_release(self, owner_key, token):  # token 精确匹配才删；key 不存在视为幂等成功
        current = self.values.get(owner_key)
        if current is not None and current != token:
            return 0
        self.values.pop(owner_key, None)
        return 1

    def _eval_renew(self, owner_key, token):
        return 1 if self.values.get(owner_key) == token else 0

    def _eval_takeover(self, keys, argv):
        # P1-DR-02 takeover：holder token 复核 + holder 权威 lease 存活判定。
        token, expected_holder, holder_lease = argv[0], argv[5], argv[6]
        current = self.values.get(keys[0])
        if current is not None and current != token:
            if current != expected_holder or self.lease.get(keys[2]) == holder_lease:
                return 0
        self.values[keys[0]] = token
        return 1

    def _eval_claim(self, owner_key, *, token, worker_id):
        # claim：空槽 / 幂等重试 / 同 worker 旧代际接管三种情形写入 token。
        current = self.values.get(owner_key)
        if current is None or current == token or current.startswith(f"{worker_id}:"):
            self.values[owner_key] = token
            return 1
        return 0


class _Service(RunOwnershipRpcMixin):
    def __init__(self, *, current: bool = True) -> None:
        self._lease_verifier = AsyncMock(return_value=current)


def _context() -> MagicMock:
    return MagicMock(abort=AsyncMock())


def _claim(**overrides):
    values = {
        "worker_id": "worker-1",
        "lease_id": "lease-1",
        "run_id": "run-1",
        "ttl_ms": MAX_RUN_OWNERSHIP_TTL_MS,
    }
    values.update(overrides)
    return artifact_pb2.RunOwnershipClaimRequest(**values)


def _install(monkeypatch, redis: _Redis) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    # P1-GW-02 新契约：claim 预检只验 worker 归属（owns_runs），fence
    # ACQUIRED 之后才落 PG 绑定（bind_generation）；renew/release 要求已绑定
    # 代际（owns_runs_for_lease）。
    bind_generation = AsyncMock()
    owns_runs = AsyncMock()
    owns_runs_for_lease = AsyncMock()
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(module, "require_authenticated_worker", AsyncMock(side_effect=lambda _ctx, worker: worker))
    monkeypatch.setattr(module, "bind_worker_run_lease_generation", bind_generation)
    monkeypatch.setattr(module, "require_worker_owns_runs", owns_runs)
    monkeypatch.setattr(module, "require_worker_owns_runs_for_lease", owns_runs_for_lease)
    monkeypatch.setattr(fence, "redis_namespace", lambda ns=None: ns or "tenant-a")
    # P1-GW-04 修正后 _bind_lease_generation 会通过 LeaseStore.get() 拿
    # granted_at_ms 作 gen; LeaseStore 内部用 module.redis_namespace(),这里
    # 也要 patch 让 lease_key 命名空间与 fence 侧一致。
    monkeypatch.setattr(module, "redis_namespace", lambda ns=None: ns or "tenant-a")
    lease_key = fence._lease_key("worker-1", None)
    redis.lease[lease_key] = "lease-1"
    # P1-GW-04: LeaseStore.get() 读 hgetall, 提供 granted_at_ms=100 作固定 gen
    redis.lease_hash[lease_key] = {
        "lease_id": "lease-1",
        "granted_at_ms": "100",
        "expires_at_ms": "999999999",
    }
    return bind_generation, owns_runs, owns_runs_for_lease


@pytest.mark.asyncio
async def test_claim_is_idempotent_for_same_lease(monkeypatch):
    redis = _Redis()
    _install(monkeypatch, redis)
    service = _Service()

    first = await service.ClaimRunOwnership(_claim(), _context())
    retry = await service.ClaimRunOwnership(_claim(), _context())

    assert first.acquired is True
    assert retry.acquired is True
    assert redis.values == {"{tenant-a}:run:owner:run-1": "worker-1:lease-1"}


@pytest.mark.asyncio
async def test_claim_with_superseded_lease_aborts_failed_precondition(monkeypatch):
    # P1-GW-04: 入口预检通过后代际切换（TOCTOU），fence Lua 返回 LEASE_STALE，
    # RPC 必须 abort FAILED_PRECONDITION，绝不允许旧代际写 ownership。
    # P1-GW-02: 此时 PG 绑定绝不能发生（fence 未 ACQUIRED）。
    redis = _Redis()
    bind_generation, _owns, _owns_lease = _install(monkeypatch, redis)
    service = _Service()
    context = _context()

    response = await service.ClaimRunOwnership(_claim(lease_id="lease-old"), context)

    assert response.acquired is False
    assert context.abort.await_args.args[0] == grpc.StatusCode.FAILED_PRECONDITION
    assert redis.values == {}
    bind_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_generation_takes_over_same_worker_stale_token(monkeypatch):
    # 同 worker 的旧代际 token 不再阻塞新代际最长 65 分钟。
    redis = _Redis()
    _install(monkeypatch, redis)
    redis.values["{tenant-a}:run:owner:run-1"] = "worker-1:lease-dead"
    service = _Service()

    response = await service.ClaimRunOwnership(_claim(), _context())

    assert response.acquired is True
    assert redis.values == {"{tenant-a}:run:owner:run-1": "worker-1:lease-1"}


@pytest.mark.asyncio
async def test_claim_held_by_other_live_worker_returns_not_acquired(monkeypatch):
    # holder（worker-2）的权威 lease 仍现行 → 不得接管（P1-DR-02）。
    redis = _Redis()
    _install(monkeypatch, redis)
    redis.values["{tenant-a}:run:owner:run-1"] = "worker-2:lease-x"
    redis.lease[fence._lease_key("worker-2", None)] = "lease-x"
    service = _Service()
    context = _context()

    response = await service.ClaimRunOwnership(_claim(), context)

    assert response.acquired is False
    context.abort.assert_not_awaited()
    assert redis.values == {"{tenant-a}:run:owner:run-1": "worker-2:lease-x"}


@pytest.mark.asyncio
async def test_claim_takes_over_dead_other_worker(monkeypatch):
    # P1-DR-02: holder（worker-2）lease 已消失（崩溃）→ 原子接管，
    # 不再等最长 65 分钟 ownership TTL。
    redis = _Redis()
    _install(monkeypatch, redis)
    redis.values["{tenant-a}:run:owner:run-1"] = "worker-2:lease-dead"
    service = _Service()

    response = await service.ClaimRunOwnership(_claim(), _context())

    assert response.acquired is True
    assert redis.values == {"{tenant-a}:run:owner:run-1": "worker-1:lease-1"}


@pytest.mark.asyncio
async def test_renew_and_release_require_matching_token_and_release_is_idempotent(monkeypatch):
    redis = _Redis()
    _install(monkeypatch, redis)
    service = _Service()
    await service.ClaimRunOwnership(_claim(), _context())
    renew = artifact_pb2.RunOwnershipRenewRequest(
        worker_id="worker-1",
        lease_id="lease-1",
        run_id="run-1",
        ttl_ms=MAX_RUN_OWNERSHIP_TTL_MS,
    )
    release = artifact_pb2.RunOwnershipReleaseRequest(
        worker_id="worker-1",
        lease_id="lease-1",
        run_id="run-1",
    )

    assert (await service.RenewRunOwnership(renew, _context())).renewed is True
    assert (await service.ReleaseRunOwnership(release, _context())).released is True
    assert (await service.ReleaseRunOwnership(release, _context())).released is True
    assert redis.values == {}


@pytest.mark.asyncio
async def test_stale_lease_is_rejected_before_taskrun_and_redis(monkeypatch):
    redis = _Redis()
    bind_generation, owns_runs, _owns_lease = _install(monkeypatch, redis)
    context = _context()

    response = await _Service(current=False).ClaimRunOwnership(_claim(), context)

    assert response.acquired is False
    assert context.abort.await_args.args[0] == grpc.StatusCode.FAILED_PRECONDITION
    bind_generation.assert_not_awaited()
    owns_runs.assert_not_awaited()
    assert redis.eval_calls == []


@pytest.mark.asyncio
async def test_foreign_taskrun_is_rejected_before_redis(monkeypatch):
    redis = _Redis()
    bind_generation, owns_runs, _owns_lease = _install(monkeypatch, redis)
    owns_runs.side_effect = PermissionError("foreign run")
    context = _context()

    response = await _Service().ClaimRunOwnership(_claim(), context)

    assert response.acquired is False
    assert context.abort.await_args.args[0] == grpc.StatusCode.PERMISSION_DENIED
    assert redis.eval_calls == []
    bind_generation.assert_not_awaited()


# P0-03a: 以下 4 个测试(claim_binds_pg / claim_held_by_other_does_not_bind /
# bind_failure_after_fence_aborts / invalid_ttl_is_rejected)已拆到
# tests/unit/gateway/test_run_ownership_rpc_bind_cas.py, 让本文件保持 <300 行。
