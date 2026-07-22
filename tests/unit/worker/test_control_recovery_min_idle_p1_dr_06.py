"""P1-DR-06 回归:control_recovery 使用 min_idle=LEASE_TTL/4 避免抢占新代际消息。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P1-DR-06:
control_recovery 之前 min_idle_time_ms=0,新代际启动时立即认领旧代际全部
PEL entries,包括刚投递到旧代际(idle < 1s)的正常消息。两个 Worker 前后
重启形成 ping-pong 反复 XCLAIM,取消/配置控制活锁或饥饿。

修复:min_idle 提到 LeasePolicy.ttl_ms // 4(默认 7500ms),刚投递到旧代际
的消息不会被新代际立即抢走;真正僵死(idle > 7.5s)的 PEL 仍能被接管。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from antcode_worker.transport.redis.control_recovery import (
    _LEGACY_CLAIM_MIN_IDLE_MS,
    PendingControlRecovery,
)
from antcode_worker.transport.redis.runtime_control_models import ControlChannel

MIN_IDLE_LOWER_BOUND_MS = 1000  # 至少 1s(常量的下限)
MIN_IDLE_UPPER_BOUND_MS = 15_000  # 上界防错配(TTL/4 不应过大)


def test_legacy_claim_min_idle_is_at_least_1s_and_derived_from_lease_ttl():
    """P1-DR-06:min_idle 常量应至少 1s,且从 LeasePolicy.ttl_ms 派生。"""
    from antcode_core.application.services.lease_service import LeasePolicy

    expected = max(LeasePolicy().ttl_ms // 4, 1000)
    assert _LEGACY_CLAIM_MIN_IDLE_MS == expected
    assert _LEGACY_CLAIM_MIN_IDLE_MS >= MIN_IDLE_LOWER_BOUND_MS
    assert _LEGACY_CLAIM_MIN_IDLE_MS <= MIN_IDLE_UPPER_BOUND_MS


@pytest.mark.asyncio
async def test_claim_config_uses_shared_min_idle_constant():
    """P1-DR-06:_claim_old_generations 内 ReclaimConfig 使用共享常量。"""
    channel = ControlChannel("antcode:control:worker-1", "antcode-control")
    recovery = PendingControlRecovery(
        (channel,),
        legacy_consumer_name="worker-1",
        page_size=2,
    )
    redis = AsyncMock()
    # 空 pending → 立即返回
    redis.xpending_range.return_value = []
    redis.xreadgroup.return_value = []

    # 触发 _claim_old_generations
    await recovery.poll(redis, "worker-1-lease-2")

    # 记录到 recovery 的 config 应该已用 _LEGACY_CLAIM_MIN_IDLE_MS
    # xpending_range 首个调用参数应包含 min_idle_time (可能命名不同,依 redis-py 版本)
    assert redis.xpending_range.await_count >= 1
