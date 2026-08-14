"""Metrics and lifecycle operations for the Crawl Redis queue."""

from collections.abc import Callable

from loguru import logger

from antcode_core.application.services.crawl.backends.base import QueueMetrics, QueueStats
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.stream_client import StreamClient


class RedisQueueMetricsMixin:
    _stream_client: StreamClient
    _consumer_group: str
    _stream_key: Callable[[str, int], str]
    _dead_letter_key: Callable[[str], str]
    _deleted_fence_key: Callable[[str], str]

    async def stats(self, project_id: str) -> QueueStats:
        pending = 0
        processing = 0
        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            metrics = await self.get_queue_metrics(project_id, priority)
            pending += metrics.queue_length
            processing += metrics.pending_count
        return QueueStats(
            pending=pending,
            processing=processing,
            total=pending + processing,
            dead_letter=await self.get_dead_letter_count(project_id),
        )

    async def get_queue_metrics(self, project_id: str, priority: int) -> QueueMetrics:
        stream_key = self._stream_key(project_id, priority)
        stream_length = await self._stream_client.xlen(stream_key)
        pending = await self._stream_client.xpending(stream_key, group_name=self._consumer_group)
        pending_count = pending.get("pending_count", 0)
        return QueueMetrics(
            queue_length=max(stream_length - pending_count, 0),
            pending_count=pending_count,
            consumers=pending.get("consumers", {}),
        )

    async def ensure_queues(self, project_id: str) -> bool:
        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            await self._stream_client.ensure_active_group(
                self._stream_key(project_id, priority),
                self._consumer_group,
                deleted_fence_key=self._deleted_fence_key(project_id),
            )
        logger.debug(f"确保队列存在: project={project_id}")
        return True

    async def clear_queues(self, project_id: str) -> bool:
        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            await self._stream_client.delete_stream(self._stream_key(project_id, priority))
        await self._stream_client.delete_stream(self._dead_letter_key(project_id))
        logger.info(f"清空队列: project={project_id}")
        return True

    async def get_queue_length(self, project_id: str, priority: int | None = None) -> int:
        if priority is not None:
            metrics = await self.get_queue_metrics(project_id, priority)
            return metrics.queue_length
        lengths = [
            (await self.get_queue_metrics(project_id, item)).queue_length
            for item in [Priority.HIGH, Priority.NORMAL, Priority.LOW]
        ]
        return sum(lengths)

    async def get_pending_count(self, project_id: str, priority: int | None = None) -> int:
        priorities = [priority] if priority is not None else [Priority.HIGH, Priority.NORMAL, Priority.LOW]
        total = 0
        for item in priorities:
            info = await self._stream_client.xpending(
                self._stream_key(project_id, item),
                group_name=self._consumer_group,
            )
            total += info.get("pending_count", 0)
        return total

    async def get_dead_letter_count(self, project_id: str) -> int:
        return await self._stream_client.xlen(self._dead_letter_key(project_id))
