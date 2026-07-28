"""outbox 消费 claim 的类型与活跃判定（从 outbox_service 拆出）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from tortoise.expressions import Q

from antcode_core.domain.models.scheduler_outbox import SchedulerOutbox

OUTBOX_REPUBLISH_SECONDS = 60


class OutboxConsumeClaim(StrEnum):
    CLAIMED = "claimed"
    BUSY = "busy"
    CONSUMED = "consumed"


def claim_is_active(event: SchedulerOutbox, stale_before: datetime) -> bool:
    return bool(
        event.consume_owner and event.consume_started_at is not None and event.consume_started_at > stale_before
    )


def replay_filter(now: datetime) -> Q:
    replay_before = now - timedelta(seconds=OUTBOX_REPUBLISH_SECONDS)
    return Q(published_at__isnull=True) | Q(published_at__lte=replay_before)


__all__ = ["OUTBOX_REPUBLISH_SECONDS", "OutboxConsumeClaim", "claim_is_active", "replay_filter"]
