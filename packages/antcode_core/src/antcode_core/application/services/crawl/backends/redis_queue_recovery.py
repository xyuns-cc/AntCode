"""PEL recovery and dead-letter settlement for the Crawl Redis queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from antcode_core.application.services.crawl.backends.base import QueueTask, ReclaimedTask
from antcode_core.application.services.crawl.backends.redis_queue_payloads import (
    dead_letter_payload,
    invalid_message_payload,
)
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.stream_client import StreamClient, StreamMessage

DEAD_LETTER_TTL_SECONDS = 7 * 24 * 60 * 60
RECLAIMER_CONSUMER = "reclaimer"
FIRST_STREAM_ID = "0-0"


@dataclass(frozen=True)
class CrawlQueueLocation:
    project_id: str
    priority: int
    stream_key: str


@dataclass(frozen=True)
class ReclaimRequest:
    location: CrawlQueueLocation
    min_idle_ms: int
    count: int


class RedisQueueRecoveryMixin:
    _stream_client: StreamClient
    _consumer_group: str
    _max_stream_len: int
    _reclaim_cursors: dict[str, str]
    _stream_key: Callable[[str, int], str]
    _dead_letter_key: Callable[[str], str]
    _deleted_fence_key: Callable[[str], str]

    def _location(self, project_id: str, priority: int) -> CrawlQueueLocation:
        return CrawlQueueLocation(project_id, priority, self._stream_key(project_id, priority))

    async def reclaim(
        self,
        project_id: str,
        min_idle_ms: int = 300000,
        count: int = 100,
    ) -> list[ReclaimedTask]:
        reclaimed: list[ReclaimedTask] = []
        locations = [
            self._location(project_id, priority) for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]
        ]
        for location in locations:
            await self._stream_client.ensure_active_group(
                location.stream_key,
                self._consumer_group,
                deleted_fence_key=self._deleted_fence_key(project_id),
            )
        for location in locations:
            remaining = count - len(reclaimed)
            if remaining <= 0:
                break
            request = ReclaimRequest(location, min_idle_ms, remaining)
            reclaimed.extend(await self._reclaim_stream(request))
        if reclaimed:
            logger.info(f"回收超时任务: project={project_id}, count={len(reclaimed)}")
        return reclaimed

    async def _reclaim_stream(self, request: ReclaimRequest) -> list[ReclaimedTask]:
        stream_key = request.location.stream_key
        cursor = self._reclaim_cursors.get(stream_key, FIRST_STREAM_ID)
        reclaimed: list[ReclaimedTask] = []
        while len(reclaimed) < request.count:
            next_id, messages, deleted = await self._claim_page(request, cursor, len(reclaimed))
            self._reclaim_cursors[stream_key] = next_id
            self._log_deleted(request.location, deleted)
            for message in messages:
                item = await self._decode_reclaimed(request.location, message)
                if item is not None:
                    reclaimed.append(item)
            if next_id == FIRST_STREAM_ID:
                break
            if next_id == cursor:
                raise RuntimeError(f"XAUTOCLAIM 游标未前进: stream={stream_key}, cursor={cursor}")
            cursor = next_id
        return reclaimed

    async def _claim_page(self, request: ReclaimRequest, cursor: str, reclaimed_count: int):
        return await self._stream_client.xautoclaim(
            request.location.stream_key,
            group_name=self._consumer_group,
            consumer_name=RECLAIMER_CONSUMER,
            min_idle_time_ms=request.min_idle_ms,
            start_id=cursor,
            count=request.count - reclaimed_count,
        )

    @staticmethod
    def _log_deleted(location: CrawlQueueLocation, deleted_ids: list[str]) -> None:
        if deleted_ids:
            logger.info(
                f"清理无正文 PEL: project={location.project_id}, priority={location.priority}, count={len(deleted_ids)}"
            )

    async def _decode_reclaimed(
        self,
        location: CrawlQueueLocation,
        message: StreamMessage,
    ) -> ReclaimedTask | None:
        try:
            task = QueueTask.from_dict(message.data, message.msg_id)
        except (TypeError, ValueError) as exc:
            await self._dead_letter_invalid(
                location,
                msg_id=message.msg_id,
                data=message.data,
                error=exc,
            )
            return None
        task.priority = location.priority
        task.project_id = location.project_id
        pending = await self._stream_client.xpending_range(
            location.stream_key,
            group_name=self._consumer_group,
            start=message.msg_id,
            end=message.msg_id,
            count=1,
        )
        delivery_count = pending[0].delivery_count if pending else 1
        return ReclaimedTask(task=task, delivery_count=delivery_count)

    async def _dead_letter_invalid(
        self,
        location: CrawlQueueLocation,
        *,
        msg_id: str,
        data: dict,
        error: Exception,
    ) -> None:
        moved = await self._stream_client.move_pending(
            location.stream_key,
            self._dead_letter_key(location.project_id),
            group_name=self._consumer_group,
            msg_id=msg_id,
            data=invalid_message_payload(msg_id, data, error),
            maxlen=self._max_stream_len,
            expire_seconds=DEAD_LETTER_TTL_SECONDS,
            deleted_fence_key=self._deleted_fence_key(location.project_id),
        )
        if moved is None:
            raise RuntimeError(f"坏 Crawl 消息不再属于当前 PEL: msg_id={msg_id}")
        logger.warning(f"坏 Crawl 消息已隔离到 DLQ: project={location.project_id}, msg_id={msg_id}")

    async def requeue_claimed(self, project_id: str, task: QueueTask) -> str | None:
        source_key = self._stream_key(project_id, task.priority)
        retry_task = QueueTask.from_dict({**task.to_dict(), "status": "pending"})
        return await self._stream_client.move_pending(
            source_key,
            source_key,
            group_name=self._consumer_group,
            msg_id=task.msg_id,
            data=retry_task.to_dict(),
            deleted_fence_key=self._deleted_fence_key(project_id),
        )

    async def dead_letter_claimed(self, project_id: str, task: QueueTask) -> str | None:
        return await self._stream_client.move_pending(
            self._stream_key(project_id, task.priority),
            self._dead_letter_key(project_id),
            group_name=self._consumer_group,
            msg_id=task.msg_id,
            data=dead_letter_payload(task, "max_retries_exceeded"),
            maxlen=self._max_stream_len,
            expire_seconds=DEAD_LETTER_TTL_SECONDS,
            deleted_fence_key=self._deleted_fence_key(project_id),
        )

    async def move_to_dead_letter(self, project_id: str, tasks: list[QueueTask]) -> int:
        moved = 0
        for task in tasks:
            if not task.msg_id:
                raise ValueError("死信迁移要求源 PEL message id")
            moved += int(await self.dead_letter_claimed(project_id, task) is not None)
        logger.info(f"移入死信队列: project={project_id}, count={moved}")
        return moved
