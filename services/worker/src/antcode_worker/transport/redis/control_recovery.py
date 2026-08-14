"""Lease-generation-aware PEL recovery for Direct control channels."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

from antcode_core.application.services.lease_service import wire_lease_policy

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
_LEGACY_CLAIM_MIN_IDLE_MS = max(wire_lease_policy().ttl_ms // 4, 1000)
_CONTROL_RECOVERY_INTERVAL_SECONDS = _LEGACY_CLAIM_MIN_IDLE_MS / 1000


class PendingControlRecovery(PendingChannelRecovery):
    """Claim old lease consumers, then drain the current control consumer PEL."""

    def __init__(
        self,
        channels: tuple[ControlChannel, ...],
        *,
        legacy_consumer_name: str,
        require_current_generation: Callable[[], Awaitable[None]],
        page_size: int = DEFAULT_RECOVERY_PAGE_SIZE,
        recovery_interval_seconds: float = _CONTROL_RECOVERY_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        is_in_flight: Any = None,
    ) -> None:
        if (
            isinstance(recovery_interval_seconds, bool)
            or not isinstance(recovery_interval_seconds, (int, float))
            or not math.isfinite(recovery_interval_seconds)
            or recovery_interval_seconds <= 0
        ):
            raise ValueError("control PEL recovery_interval_seconds 必须大于 0")
        self._legacy_consumer_name = legacy_consumer_name
        self._require_current_generation = require_current_generation
        self._recovery_interval_seconds = recovery_interval_seconds
        self._monotonic = monotonic
        self._next_recovery_at = 0.0
        if is_in_flight is None:
            super().__init__(channels, page_size=page_size)
        else:
            super().__init__(channels, page_size=page_size, is_in_flight=is_in_flight)

    def reset(self) -> None:
        self._next_recovery_at = 0.0
        super().reset()

    async def poll(
        self,
        redis: Any,
        consumer_name: str,
    ) -> tuple[str, str, dict[Any, Any]] | None:
        now = self._monotonic()
        if consumer_name != self._legacy_consumer_name and now >= self._next_recovery_at:
            await self._claim_old_generations(redis, consumer_name)
            PendingChannelRecovery.reset(self)
            self._next_recovery_at = now + self._recovery_interval_seconds
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
                require_current_generation=self._require_current_generation,
            )
            while await claimer.find_and_claim(channel.stream_key, consumer_name):
                pass
