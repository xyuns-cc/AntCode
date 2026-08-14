"""Read-side dependency for manual retry request idempotency."""

from __future__ import annotations

from typing import Any

from antcode_core.domain.models.scheduler_outbox import SchedulerOutbox


async def get_manual_retry_event(public_id: str, connection: Any) -> SchedulerOutbox | None:
    return await SchedulerOutbox.filter(public_id=public_id).using_db(connection).first()


__all__ = ["get_manual_retry_event"]
