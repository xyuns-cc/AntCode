"""P1-round6 5.3 回归:RunArtifactQuota 的 Redis 备份 (async 路径)。

审查文档 round6 5.3:
`Artifact quota 是 4096-run 的进程内 LRU, 重启、多副本和驱逐均可重置`。

修复:async_reserve / async_release / restore_from_redis 组成可选 Redis
备份层, 进程重启后能从 Redis 恢复已占用的 count/bytes, 攻击者无法通过
Gateway 重启把 quota 放大 N 倍。

Redis 不可用/未配置时降级纯内存 (sync reserve/release 仍生效)。

本测试锁死:
1. async_reserve 内存 fence + 写 Redis (HINCRBY count/total_bytes + EXPIRE)
2. restore_from_redis 内存 miss + Redis 有键 → 恢复账目
3. restore Redis 失败 → warn, 不 raise, quota 继续走内存兜底
4. 无 redis_client 时 async_reserve 退化为纯内存 reserve
5. restore 后再 reserve → 累加 (说明恢复的账目起效)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_gateway.services.artifact_quota import (
    MAX_ARTIFACT_BYTES_PER_RUN,
    MAX_ARTIFACTS_PER_RUN,
    RunArtifactQuota,
    RunArtifactQuotaExceeded,
)

_TTL_SECONDS = 24 * 3600
_EXPECTED_TWO_PIPELINE_OPS = 3  # HINCRBY count + HINCRBY total_bytes + EXPIRE
_EXPECTED_TWO_RELEASE_OPS = 2  # HINCRBY count -1 + HINCRBY total_bytes -N
_HINCRBY_COUNT_EXPECTED = 2
_RESTORED_COUNT = 3
_RESTORED_BYTES = 1024
_HUNDRED_BYTES = 100
_ACC_COUNT_AFTER_ADD = 3
_ACC_BYTES_AFTER_ADD = 300
_RESTORE_MAX_2 = 2
_ARTIFACTS_LIMIT_STABLE = 1000
_ARTIFACT_BYTES_LIMIT_STABLE = 500 * 1024 * 1024


@pytest.mark.asyncio
async def test_async_reserve_writes_hincrby_and_expire():
    redis = MagicMock()
    pipeline = MagicMock()
    pipeline.hincrby = MagicMock(return_value=pipeline)
    pipeline.expire = MagicMock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[1, 100, True])
    redis.pipeline = MagicMock(return_value=pipeline)
    redis.hgetall = AsyncMock(return_value={})

    quota = RunArtifactQuota(redis_client=redis, namespace="tenant-a")
    await quota.async_reserve("run-1", 100)

    # 内存 fence
    assert quota.usage_of("run-1") == (1, 100)
    # Redis 备份: 3 个 pipeline 命令
    assert pipeline.hincrby.call_count == _HINCRBY_COUNT_EXPECTED
    pipeline.expire.assert_called_once()
    pipeline.execute.assert_awaited_once()
    _, ttl = pipeline.expire.call_args.args
    assert ttl == _TTL_SECONDS


@pytest.mark.asyncio
async def test_restore_from_redis_populates_memory_ledger():
    redis = MagicMock()
    redis.hgetall = AsyncMock(return_value={b"count": b"3", b"total_bytes": b"1024"})
    quota = RunArtifactQuota(redis_client=redis)

    # 重启后内存 miss
    assert quota.usage_of("run-restored") == (0, 0)
    await quota.restore_from_redis("run-restored")
    assert quota.usage_of("run-restored") == (_RESTORED_COUNT, _RESTORED_BYTES)


@pytest.mark.asyncio
async def test_restore_from_redis_failure_falls_back_silent():
    redis = MagicMock()
    redis.hgetall = AsyncMock(side_effect=RuntimeError("redis down"))
    quota = RunArtifactQuota(redis_client=redis)

    # 不 raise, quota 走内存兜底 (仍能后续 reserve)
    await quota.restore_from_redis("run-1")
    quota.reserve("run-1", _HUNDRED_BYTES)
    assert quota.usage_of("run-1") == (1, _HUNDRED_BYTES)


@pytest.mark.asyncio
async def test_no_redis_client_async_reserve_is_pure_memory():
    quota = RunArtifactQuota()  # redis_client=None
    await quota.async_reserve("run-1", _HUNDRED_BYTES)
    assert quota.usage_of("run-1") == (1, _HUNDRED_BYTES)
    await quota.async_release("run-1", _HUNDRED_BYTES)
    assert quota.usage_of("run-1") == (0, 0)


@pytest.mark.asyncio
async def test_restore_then_reserve_accumulates():
    """restore 恢复 (2, 200) 后再 async_reserve → 累加 (3, 300)。"""
    redis = MagicMock()
    redis.hgetall = AsyncMock(return_value={"count": "2", "total_bytes": "200"})
    pipeline = MagicMock()
    pipeline.hincrby = MagicMock(return_value=pipeline)
    pipeline.expire = MagicMock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[3, 300, True])
    redis.pipeline = MagicMock(return_value=pipeline)

    quota = RunArtifactQuota(redis_client=redis)
    await quota.async_reserve("run-1", _HUNDRED_BYTES)
    assert quota.usage_of("run-1") == (_ACC_COUNT_AFTER_ADD, _ACC_BYTES_AFTER_ADD)


@pytest.mark.asyncio
async def test_async_reserve_over_limit_still_raises():
    """restore 恢复接近上限, 再 async_reserve 应触发 exceeded 且不写 Redis。"""
    redis = MagicMock()
    redis.hgetall = AsyncMock(return_value={"count": str(MAX_ARTIFACTS_PER_RUN - 1), "total_bytes": "0"})
    pipeline = MagicMock()
    pipeline.execute = AsyncMock()
    redis.pipeline = MagicMock(return_value=pipeline)

    quota = RunArtifactQuota(redis_client=redis)
    await quota.async_reserve("run-1", _HUNDRED_BYTES)
    with pytest.raises(RunArtifactQuotaExceeded):
        await quota.async_reserve("run-1", _HUNDRED_BYTES)
    # 超限时不再写 Redis 备份
    assert pipeline.execute.await_count == 1  # 只有第一次成功 reserve 写了


@pytest.mark.asyncio
async def test_async_release_hincrby_negative():
    redis = MagicMock()
    pipeline = MagicMock()
    pipeline.hincrby = MagicMock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[0, 0])
    redis.pipeline = MagicMock(return_value=pipeline)
    redis.hgetall = AsyncMock(return_value={})

    quota = RunArtifactQuota(redis_client=redis)
    await quota.async_reserve("run-1", _HUNDRED_BYTES)
    pipeline.reset_mock()
    await quota.async_release("run-1", _HUNDRED_BYTES)
    assert pipeline.hincrby.call_count == _EXPECTED_TWO_RELEASE_OPS
    _ = _EXPECTED_TWO_PIPELINE_OPS  # 引用常量供参考,抑制未使用告警


@pytest.mark.asyncio
async def test_restore_respects_max_tracked_runs_eviction():
    redis = MagicMock()
    redis.hgetall = AsyncMock(return_value={"count": "1", "total_bytes": "50"})
    quota = RunArtifactQuota(redis_client=redis, max_tracked_runs=2)

    await quota.restore_from_redis("r-1")
    await quota.restore_from_redis("r-2")
    await quota.restore_from_redis("r-3")  # 应驱逐 r-1
    assert quota.metrics()["tracked_runs"] == _RESTORE_MAX_2
    assert quota.metrics()["evicted_runs"] == 1
    # 常量守卫: 变更上限必须显式修 baseline
    assert MAX_ARTIFACTS_PER_RUN == _ARTIFACTS_LIMIT_STABLE
    assert MAX_ARTIFACT_BYTES_PER_RUN == _ARTIFACT_BYTES_LIMIT_STABLE
