"""PostgreSQL transactional outbox and recovery publisher."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.common.config import settings
from antcode_core.domain.models.scheduler_outbox import SchedulerOutbox
from antcode_core.infrastructure.redis.streams import StreamClient

OUTBOX_POLL_SECONDS = 1.0
OUTBOX_BATCH_SIZE = 50
OUTBOX_MAX_BACKOFF_SECONDS = 300


class SchedulerOutboxService:
    """Commit scheduler events in PostgreSQL and publish them at least once."""

    def __init__(self, stream: StreamClient | None = None):
        self._stream = stream or StreamClient()
        self._running = False
        self._task: asyncio.Task | None = None

    async def enqueue(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str | int,
        payload: dict[str, Any],
        connection: Any | None = None,
    ) -> SchedulerOutbox:
        available_at = datetime.now(UTC)
        if connection is None:
            return await SchedulerOutbox.create(
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=str(aggregate_id),
                payload=dict(payload),
                available_at=available_at,
            )
        return await SchedulerOutbox.create(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            payload=dict(payload),
            available_at=available_at,
            using_db=connection,
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("scheduler outbox publisher 已启动")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run(self) -> None:
        while self._running:
            try:
                published = await self.publish_available()
                if published == 0:
                    await asyncio.sleep(OUTBOX_POLL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("scheduler outbox publisher 异常")
                await asyncio.sleep(OUTBOX_POLL_SECONDS)

    async def publish_available(self) -> int:
        published = 0
        for _ in range(OUTBOX_BATCH_SIZE):
            handled = await self._publish_one()
            if not handled:
                break
            published += 1
        return published

    async def _publish_one(self) -> bool:
        now = datetime.now(UTC)
        async with in_transaction("default") as conn:
            event = await (
                SchedulerOutbox.filter(
                    published_at__isnull=True,
                    available_at__lte=now,
                )
                .using_db(conn)
                .select_for_update(skip_locked=True)
                .order_by("id")
                .first()
            )
            if event is None:
                return False
            try:
                await self._publish(event)
            except Exception as exc:
                await self._defer(event, exc, conn)
                return True
            await (
                SchedulerOutbox.filter(id=event.id)
                .using_db(conn)
                .update(
                    published_at=now,
                    last_error=None,
                )
            )
        return True

    async def _publish(self, event: SchedulerOutbox) -> None:
        fields = dict(event.payload or {})
        fields["event"] = event.event_type
        fields["outbox_id"] = event.public_id
        fields["timestamp"] = event.created_at.isoformat()
        await self._stream.xadd(settings.scheduler_event_stream, fields)

    async def _defer(self, event: SchedulerOutbox, exc: Exception, conn: Any) -> None:
        attempts = int(event.attempts or 0) + 1
        delay = min(2 ** min(attempts, 8), OUTBOX_MAX_BACKOFF_SECONDS)
        await (
            SchedulerOutbox.filter(id=event.id)
            .using_db(conn)
            .update(
                attempts=attempts,
                available_at=datetime.now(UTC) + timedelta(seconds=delay),
                last_error=str(exc)[:2000],
            )
        )
        logger.warning(
            "scheduler outbox 发布失败，已延期: id={} attempts={} error={}",
            event.public_id,
            attempts,
            exc,
        )


scheduler_outbox_service = SchedulerOutboxService()

__all__ = ["SchedulerOutboxService", "scheduler_outbox_service"]
