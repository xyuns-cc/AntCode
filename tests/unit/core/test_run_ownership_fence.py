"""run_ownership_fence 的键布局、参数协议与结果映射合同。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.lease_service import LEASE_RECORD_RETENTION_MS
from antcode_core.application.services.workers import run_ownership_fence as fence

# fence Lua 的 numkeys 合同：claim 传 owner+lease 两个 key，takeover 额外携带 holder lease key。
_CLAIM_NUMKEYS = 2
_TAKEOVER_NUMKEYS = 3


def test_run_owner_key_shares_lease_hash_tag():
    # Redis Cluster 下 fence Lua 同时访问 ownership key 与 lease key，
    # 两者必须共用 {ns} hash tag 落同一 slot。
    assert fence.run_owner_key("run-1", "tenant-a") == "{tenant-a}:run:owner:run-1"
    assert fence._lease_key("worker-1", "tenant-a") == "{tenant-a}:lease:data:worker-1"


def test_ownership_token_format():
    assert fence.ownership_token("worker-1", "lease-9") == "worker-1:lease-9"


@pytest.mark.asyncio
async def test_claim_passes_lease_fence_arguments():
    redis = AsyncMock()
    redis.eval.return_value = 1

    outcome = await fence.claim_run_ownership(
        redis,
        worker_id="worker-1",
        lease_id="lease-9",
        run_id="run-1",
        ttl_ms=5_000,
        namespace="tenant-a",
    )

    assert outcome is fence.OwnershipOutcome.ACQUIRED
    args = redis.eval.await_args.args
    assert args[1] == _CLAIM_NUMKEYS
    assert args[2] == "{tenant-a}:run:owner:run-1"
    assert args[3] == "{tenant-a}:lease:data:worker-1"
    assert args[4] == "worker-1:lease-9"
    assert args[5] == "5000"
    assert args[6] == "worker-1"
    assert args[7] == "lease-9"
    assert args[8] == str(LEASE_RECORD_RETENTION_MS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, fence.OwnershipOutcome.ACQUIRED),
        (0, fence.OwnershipOutcome.HELD_BY_OTHER),
        (-1, fence.OwnershipOutcome.LEASE_STALE),
    ],
)
async def test_renew_outcome_mapping(raw, expected):
    redis = AsyncMock()
    redis.eval.return_value = raw

    outcome = await fence.renew_run_ownership(
        redis,
        worker_id="worker-1",
        lease_id="lease-9",
        run_id="run-1",
        ttl_ms=5_000,
        namespace="tenant-a",
    )

    assert outcome is expected


@pytest.mark.asyncio
async def test_unknown_script_result_raises():
    redis = AsyncMock()
    redis.eval.return_value = 7

    with pytest.raises(RuntimeError, match="未知结果"):
        await fence.claim_run_ownership(
            redis,
            worker_id="worker-1",
            lease_id="lease-9",
            run_id="run-1",
            ttl_ms=5_000,
            namespace="tenant-a",
        )


@pytest.mark.asyncio
async def test_release_uses_single_key_token_match():
    redis = AsyncMock()
    redis.eval.return_value = 1

    released = await fence.release_run_ownership(
        redis,
        worker_id="worker-1",
        lease_id="lease-9",
        run_id="run-1",
        namespace="tenant-a",
    )

    assert released is True
    args = redis.eval.await_args.args
    assert args[1] == 1
    assert args[2] == "{tenant-a}:run:owner:run-1"
    assert args[3] == "worker-1:lease-9"


def test_claim_script_contains_lease_fence_and_takeover():
    # 脚本文本合同：权威 lease 校验、PTTL retention 窗口、同 worker 接管。
    script = fence._CLAIM_SCRIPT
    assert "HGET" in script and "'lease_id'" in script
    assert "PTTL" in script
    assert "string.sub(current, 1, string.len(prefix)) == prefix" in script
    # ACL 约束：worker 只被授予 +set（覆盖 SET ... PX），不得使用 PSETEX。
    assert "PSETEX" not in script


def test_takeover_script_checks_holder_lease_and_expected_token():
    # P1-DR-02 脚本文本合同：holder token 复核（防 GET→EVAL 换主）+
    # holder 权威 lease 存活判定；同样不得使用 PSETEX。
    script = fence._TAKEOVER_SCRIPT
    assert "ARGV[6]" in script and "ARGV[7]" in script
    assert "KEYS[3]" in script
    assert "PSETEX" not in script


@pytest.mark.asyncio
async def test_claim_takes_over_dead_holder():
    # P1-DR-02: holder 崩溃（lease 已消失/换代）→ 原子接管，不等 65 分钟 TTL。
    redis = AsyncMock()
    redis.eval.side_effect = [0, 1]
    redis.get.return_value = b"worker-2:lease-dead"

    outcome = await fence.claim_run_ownership(
        redis,
        worker_id="worker-1",
        lease_id="lease-9",
        run_id="run-1",
        ttl_ms=5_000,
        namespace="tenant-a",
    )

    assert outcome is fence.OwnershipOutcome.ACQUIRED
    takeover_args = redis.eval.await_args_list[1].args
    assert takeover_args[0] == fence._TAKEOVER_SCRIPT
    assert takeover_args[1] == _TAKEOVER_NUMKEYS
    assert takeover_args[2] == "{tenant-a}:run:owner:run-1"
    assert takeover_args[3] == "{tenant-a}:lease:data:worker-1"
    assert takeover_args[4] == "{tenant-a}:lease:data:worker-2"
    assert takeover_args[5] == "worker-1:lease-9"
    assert takeover_args[10] == "worker-2:lease-dead"
    assert takeover_args[11] == "lease-dead"


@pytest.mark.asyncio
async def test_claim_stays_held_by_other_when_holder_alive():
    redis = AsyncMock()
    redis.eval.side_effect = [0, 0]
    redis.get.return_value = "worker-2:lease-live"

    outcome = await fence.claim_run_ownership(
        redis,
        worker_id="worker-1",
        lease_id="lease-9",
        run_id="run-1",
        ttl_ms=5_000,
        namespace="tenant-a",
    )

    assert outcome is fence.OwnershipOutcome.HELD_BY_OTHER


@pytest.mark.asyncio
async def test_claim_retries_plain_claim_when_holder_vanished():
    # GET 时 holder key 已过期释放 → 直接重试普通 claim。
    redis = AsyncMock()
    redis.eval.side_effect = [0, 1]
    redis.get.return_value = None

    outcome = await fence.claim_run_ownership(
        redis,
        worker_id="worker-1",
        lease_id="lease-9",
        run_id="run-1",
        ttl_ms=5_000,
        namespace="tenant-a",
    )

    assert outcome is fence.OwnershipOutcome.ACQUIRED
    assert redis.eval.await_args_list[1].args[0] == fence._CLAIM_SCRIPT
