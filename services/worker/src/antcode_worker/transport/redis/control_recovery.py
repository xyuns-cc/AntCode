"""Lease-generation-aware PEL recovery for Direct control channels."""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.lease_service import LeasePolicy

from antcode_worker.transport.redis.pending_recovery import (
    DEFAULT_RECOVERY_PAGE_SIZE,
    PendingChannelRecovery,
)
from antcode_worker.transport.redis.reclaim_generation import GenerationPendingClaimer
from antcode_worker.transport.redis.reclaim_models import ReclaimConfig
from antcode_worker.transport.redis.runtime_control_models import ControlChannel

# P1-DR-06: min_idle 从 0 提到 LEASE_TTL/4。原 min_idle=0 让新代际启动时立即
# 认领旧代际全部 PEL entries,包括刚刚被投递到旧代际(消息 idle < 1s)的正常
# 消息;两个 Worker 前后重启形成 ping-pong 反复 XCLAIM。用 TTL/4(默认 30s/4
# = 7500ms) 作为下限,既保证新代际能在合理时间内接手真正僵死的 PEL,又
# 避免抢占仍在正常处理中的消息。reclaim_generation 内部还会按 consumer name
# 二次过滤,只处理"非当前代际"consumer 的 entries,这里的 idle 门槛是叠加防线。
_LEGACY_CLAIM_MIN_IDLE_MS = max(LeasePolicy().ttl_ms // 4, 1000)


class PendingControlRecovery(PendingChannelRecovery):
    """Claim old lease consumers, then drain the current control consumer PEL."""

    def __init__(
        self,
        channels: tuple[ControlChannel, ...],
        *,
        legacy_consumer_name: str,
        page_size: int = DEFAULT_RECOVERY_PAGE_SIZE,
    ) -> None:
        self._legacy_consumer_name = legacy_consumer_name
        self._old_generations_claimed = False
        super().__init__(channels, page_size=page_size)

    def reset(self) -> None:
        self._old_generations_claimed = False
        super().reset()

    async def poll(
        self,
        redis: Any,
        consumer_name: str,
    ) -> tuple[str, str, dict[Any, Any]] | None:
        if not self._old_generations_claimed and consumer_name != self._legacy_consumer_name:
            await self._claim_old_generations(redis, consumer_name)
            self._old_generations_claimed = True
        return await super().poll(redis, consumer_name)

    async def _claim_old_generations(self, redis: Any, consumer_name: str) -> None:
        # P1-DR-06: idle > _LEGACY_CLAIM_MIN_IDLE_MS 才认领,防新旧代际 ping-pong
        config = ReclaimConfig(
            max_reclaim_count=self._page_size,
            min_idle_time_ms=_LEGACY_CLAIM_MIN_IDLE_MS,
        )
        for channel in self._channels:
            claimer = GenerationPendingClaimer(
                redis,
                consumer_group=channel.group,
                config=config,
                require_current_generation=_generation_already_checked,
            )
            while await claimer.find_and_claim(channel.stream_key, consumer_name):
                pass


async def _generation_already_checked() -> None:
    return None
