"""P1-SM-03 (round6) 回归:cancel_tombstones Redis 持久化防重启丢 fence。

审查文档 round6 5.2:
`tombstone 仍是进程内 600 秒并在结算前单次 pop; 重启/超时/ACK 失败会
失去取消 fence`。

Bug 场景:
- Worker 收到 cancel 但 task 消息未到, 内存记 tombstone
- Worker 重启(OOM/deploy)
- 原 task 消息经 PEL XAUTOCLAIM 回到本 Worker(或其他 Worker)
- 新进程内存 tombstone 已清空 → 未拦截 → 任务被执行
- 用户看到"已取消"但任务实际跑了

修复:record/consume 同步双写 Redis(SET EX 600 / GETDEL 原子);Worker 重启
后 consume 内存 miss 走 Redis 幸存 tombstone,fence 保留。

本测试锁死:
1. record 同步写 Redis (SET EX 600)
2. consume 内存命中优先 + Redis 双清
3. consume 内存 miss 走 Redis GETDEL (Worker 重启后 fence 幸存)
4. Redis 无 client 时降级纯内存 (向后兼容)
5. Redis 失败不阻塞本地 fence (仍能 fallback 内存)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from antcode_worker.engine.cancel_tombstones import CANCEL_TOMBSTONE_TTL_SECONDS, CancelTombstones

WORKER_ID = "worker-1"


@pytest.mark.asyncio
async def test_record_writes_to_redis_with_ttl():
    """P1-SM-03: record 应同步写 Redis SET EX 600。"""
    redis = AsyncMock()
    tomb = CancelTombstones(redis_client=redis, namespace="tenant-a", worker_id=WORKER_ID)

    await tomb.record("run-1", reason="user-cancel")

    redis.set.assert_awaited_once()
    call = redis.set.await_args
    assert call.args[0] == "{tenant-a}:cancel:tombstone:worker-1:run-1"
    assert call.args[1] == "1"
    assert call.kwargs["ex"] == int(CANCEL_TOMBSTONE_TTL_SECONDS)


@pytest.mark.asyncio
async def test_consume_memory_hit_also_clears_redis():
    """P1-SM-03: 内存命中的 consume 也要清 Redis 备份,防重启后再命中。"""
    redis = AsyncMock()
    tomb = CancelTombstones(redis_client=redis, namespace="ns", worker_id=WORKER_ID)

    await tomb.record("run-1", reason="x")
    redis.reset_mock()

    result = await tomb.consume("run-1")

    assert result is True
    redis.delete.assert_awaited_once_with("{ns}:cancel:tombstone:worker-1:run-1")


@pytest.mark.asyncio
async def test_consume_memory_miss_falls_back_to_redis_getdel():
    """P1-SM-03 关键:Worker 重启后内存空,查 Redis GETDEL 幸存 fence。"""
    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=b"1")  # Redis 里有 tombstone
    tomb = CancelTombstones(redis_client=redis, namespace="ns", worker_id=WORKER_ID)
    # 内存 dict 为空模拟重启后

    result = await tomb.consume("run-restored")

    assert result is True, "Worker 重启后应从 Redis 恢复 tombstone fence"
    redis.getdel.assert_awaited_once_with("{ns}:cancel:tombstone:worker-1:run-restored")


@pytest.mark.asyncio
async def test_consume_no_redis_no_memory_returns_false():
    """P1-SM-03:内存 + Redis 都 miss → False (真正的新 task)。"""
    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=None)
    tomb = CancelTombstones(redis_client=redis, namespace="ns", worker_id=WORKER_ID)

    result = await tomb.consume("run-new")

    assert result is False


@pytest.mark.asyncio
async def test_no_redis_client_falls_back_to_memory_only():
    """P1-SM-03 兼容:无 redis_client 时降级纯内存 (单测/dev 场景)。"""
    tomb = CancelTombstones()  # 无 redis_client
    await tomb.record("run-1", reason="x")

    assert await tomb.consume("run-1") is True
    # 第二次 consume 已被 pop
    assert await tomb.consume("run-1") is False


@pytest.mark.asyncio
async def test_redis_set_failure_does_not_block_local_fence():
    """P1-SM-03:Redis SET 失败 (暂时不可达) 时仍写内存 fence, warn 不 raise。"""
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
    tomb = CancelTombstones(redis_client=redis, namespace="ns", worker_id=WORKER_ID)

    # 不 raise
    await tomb.record("run-1", reason="x")

    # 内存 fence 仍生效
    assert await tomb.consume("run-1") is True


@pytest.mark.asyncio
async def test_consume_redis_query_failure_falls_back_to_memory():
    """P1-SM-03:Redis GETDEL 失败 (暂时不可达) 时不 raise, 内存兜底。"""
    redis = AsyncMock()
    redis.getdel = AsyncMock(side_effect=RuntimeError("redis down"))
    tomb = CancelTombstones(redis_client=redis, namespace="ns", worker_id=WORKER_ID)
    # 内存有 tombstone
    await tomb.record("run-1", reason="x")

    # 内存路径 hit, 不 raise
    result = await tomb.consume("run-1")
    assert result is True


def test_redis_key_carries_namespace_and_worker_dimension():
    """真机事故:旧键无 {ns} hash tag 之外的 wid 维度,最小权限 ACL 拒绝 GETDEL。

    键必须同时带命名空间 hash tag 和 Worker 维度,才能被
    ``{{ns}}:cancel:tombstone:{wid}:*`` 这条 selector 覆盖。
    """
    redis = AsyncMock()
    tomb = CancelTombstones(redis_client=redis, namespace="tenant-a", worker_id="worker-7")

    key = tomb._redis_key("run-9")

    assert key == "{tenant-a}:cancel:tombstone:worker-7:run-9"


def test_redis_key_is_disjoint_across_workers_and_namespaces():
    """同一 run_id 在不同 Worker / 不同命名空间下必须落到互不相同的键。"""
    redis = AsyncMock()
    mine = CancelTombstones(redis_client=redis, namespace="ns", worker_id="worker-1")
    other_worker = CancelTombstones(redis_client=redis, namespace="ns", worker_id="worker-2")
    other_ns = CancelTombstones(redis_client=redis, namespace="ns-b", worker_id="worker-1")

    keys = {tomb._redis_key("run-1") for tomb in (mine, other_worker, other_ns)}

    assert len(keys) == len((mine, other_worker, other_ns))


def test_redis_backup_without_worker_id_fails_fast():
    """无 worker_id 时不得静默退化成无 wid 维度的共享键 —— 直接抛错。"""
    with pytest.raises(ValueError, match="worker_id"):
        CancelTombstones(redis_client=AsyncMock())


def test_redis_backup_rejects_worker_id_that_breaks_acl_scoping():
    """worker_id 含 glob 字符会撑破 ACL key pattern,构造期即拒绝。"""
    with pytest.raises(ValueError, match="worker_id"):
        CancelTombstones(redis_client=AsyncMock(), worker_id="worker-*")


def test_memory_only_mode_needs_no_worker_id():
    """Gateway 模式没有 Redis 凭据,纯内存 tombstone 不要求 worker_id。"""
    tomb = CancelTombstones()

    assert tomb._worker_id == ""
