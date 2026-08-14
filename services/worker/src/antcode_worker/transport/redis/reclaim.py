import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from antcode_worker.transport.redis.keys import RedisKeys
from antcode_worker.transport.redis.owned_stream_ack import ack_owned_stream_entry
from antcode_worker.transport.redis.reclaim_admin import (
    GlobalReclaimer,
    cleanup_dead_consumers,
    ensure_consumer_group,
)
from antcode_worker.transport.redis.reclaim_generation import GenerationPendingClaimer
from antcode_worker.transport.redis.reclaim_models import (
    ReclaimConfig,
    ReclaimedTask,
    ReclaimStats,
    decode_pending_summary,
)
from antcode_worker.transport.redis.reclaim_settlement import DeadLetterSettlement


class PendingTaskReclaimer:
    """Recover task messages owned by an older Worker lease generation."""

    def __init__(
        self,
        redis_client: Any,
        worker_id: str,
        *,
        keys: RedisKeys | None = None,
        config: ReclaimConfig | None = None,
        consumer_group: str | None = None,
        generation_guard: Callable[[], Awaitable[bool]] | None = None,
        current_consumer_name: Callable[[], str] | None = None,
        on_reclaimed: Callable[[str, dict[str, str]], Awaitable[None]] | None = None,
        on_delivery_failed: Callable[[], None] | None = None,
        available_capacity: Callable[[], int] | None = None,
    ) -> None:
        self._redis = redis_client
        self._worker_id = worker_id
        self._keys = keys or RedisKeys()
        self._config = config or ReclaimConfig()
        self._consumer_group = consumer_group or self._keys.consumer_group_name()
        self._generation_guard = generation_guard
        self._consumer_name_provider = current_consumer_name or self._legacy_consumer_name
        self._on_reclaimed = on_reclaimed
        self._on_delivery_failed = on_delivery_failed
        self._available_capacity = available_capacity
        self._stats = ReclaimStats()
        self._running = False
        self._reclaim_task: asyncio.Task | None = None
        self._claimer = GenerationPendingClaimer(
            redis_client,
            consumer_group=self._consumer_group,
            config=self._config,
            require_current_generation=self._require_current_generation,
        )
        self._settlement = DeadLetterSettlement(
            redis_client,
            consumer_group=self._consumer_group,
            config=self._config,
            require_current_generation=self._require_current_generation,
            current_consumer_name=self._current_consumer_name,
        )

    @property
    def stats(self) -> ReclaimStats:
        return self._stats

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._reclaim_task = asyncio.create_task(self._reclaim_loop())

    async def stop(self) -> None:
        self._running = False
        if self._reclaim_task is None:
            return
        self._reclaim_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reclaim_task
        self._reclaim_task = None

    async def reclaim_once(self) -> list[ReclaimedTask]:
        try:
            return await self._do_reclaim()
        except Exception:
            self._stats.reclaim_errors += 1
            raise

    async def dead_letter_owned(
        self,
        stream_key: str,
        message_id: str,
        data: dict[str, str],
    ) -> None:
        await self._require_current_generation()
        pending = await self._claimer.get_entry(stream_key, message_id)
        if pending.consumer != self._current_consumer_name():
            raise RuntimeError("拒绝 DLQ 非当前 task consumer 持有的消息")
        task = ReclaimedTask(
            message_id=message_id,
            data=data,
            idle_time_ms=pending.idle_time_ms,
            delivery_count=pending.delivery_count,
            last_delivery_time=_last_delivery_time(pending.idle_time_ms),
        )
        await self._move_to_dead_letter(stream_key, task)

    async def get_pending_count(self, stream_key: str | None = None) -> int:
        stream_key = stream_key or self._keys.task_ready_stream(self._worker_id)
        result = await self._redis.xpending(stream_key, self._consumer_group)
        if isinstance(result, dict):
            return int(result.get("pending", 0) or 0)
        return int(result[0]) if result else 0

    async def get_pending_summary(self, stream_key: str | None = None) -> dict[str, Any]:
        stream_key = stream_key or self._keys.task_ready_stream(self._worker_id)
        result = await self._redis.xpending(stream_key, self._consumer_group)
        if not result:
            return {"pending_count": 0, "min_id": None, "max_id": None, "consumers": {}}
        if isinstance(result, dict):
            return decode_pending_summary(result)
        pending_count, min_id, max_id, consumers = result
        return {
            "pending_count": pending_count,
            "min_id": min_id,
            "max_id": max_id,
            "consumers": {item[0]: item[1] for item in (consumers or [])},
        }

    async def _reclaim_loop(self) -> None:
        while self._running:
            try:
                tasks = await self._do_reclaim()
                await self._deliver_reclaimed(tasks)
            except asyncio.CancelledError:
                break
            except Exception:
                self._stats.reclaim_errors += 1
                logger.exception("Pending task reclaim 循环失败")
            if _task_is_cancelling():
                break
            try:
                await asyncio.sleep(self._config.check_interval_seconds)
            except asyncio.CancelledError:
                break

    async def _deliver_reclaimed(self, tasks: list[ReclaimedTask]) -> None:
        if self._on_reclaimed is None:
            return
        for task in tasks:
            try:
                await self._require_current_generation()
                await self._on_reclaimed(task.message_id, task.data)
            except Exception:
                self._stats.reclaim_errors += 1
                if self._on_delivery_failed is not None:
                    self._on_delivery_failed()
                logger.exception("Pending task 重投 callback 失败: message_id={}", task.message_id)

    async def _do_reclaim(self) -> list[ReclaimedTask]:
        if not await self._is_current_generation():
            return []
        capacity = self._reclaim_capacity()
        if capacity == 0:
            return []
        stream_key = self._keys.task_ready_stream(self._worker_id)
        consumer_name = self._current_consumer_name()
        messages = await self._claimer.find_and_claim(
            stream_key,
            consumer_name,
            max_count=capacity,
        )
        await self._require_current_generation()
        tasks = await self._classify_claimed(stream_key, messages, consumer_name)
        self._record_reclaim_stats(stream_key, len(tasks))
        return tasks

    def _reclaim_capacity(self) -> int:
        if self._available_capacity is None:
            return self._config.max_reclaim_count
        available = self._available_capacity()
        if isinstance(available, bool) or not isinstance(available, int) or available < 0:
            raise RuntimeError("task reclaim 可用容量必须是非负整数")
        return min(available, self._config.max_reclaim_count)

    async def _classify_claimed(
        self,
        stream_key: str,
        messages: list[tuple[str, dict[str, str]]],
        expected_consumer: str,
    ) -> list[ReclaimedTask]:
        reclaimed: list[ReclaimedTask] = []
        for message_id, message_data in messages:
            await self._require_current_generation()
            task = await self._build_reclaimed_task(
                stream_key,
                message_id,
                message_data,
                expected_consumer=expected_consumer,
            )
            retry_count = task.delivery_count - 1
            if retry_count <= self._config.max_retries:
                reclaimed.append(task)
                self._stats.total_reclaimed += 1
            else:
                await self._discard_exhausted(stream_key, task)
        return reclaimed

    async def _build_reclaimed_task(
        self,
        stream_key: str,
        message_id: str,
        data: dict[str, str],
        *,
        expected_consumer: str,
    ) -> ReclaimedTask:
        pending = await self._claimer.get_entry(stream_key, message_id)
        if pending.message_id != message_id:
            raise RuntimeError("XCLAIM 后 PEL message_id 校验失败")
        if pending.consumer != expected_consumer:
            raise RuntimeError("XCLAIM 后 PEL consumer 校验失败")
        return ReclaimedTask(
            message_id=message_id,
            data=data,
            idle_time_ms=pending.idle_time_ms,
            delivery_count=pending.delivery_count,
            last_delivery_time=_last_delivery_time(pending.idle_time_ms),
        )

    async def _discard_exhausted(self, stream_key: str, task: ReclaimedTask) -> None:
        await self._require_current_generation()
        if self._config.enable_dead_letter:
            await self._move_to_dead_letter(stream_key, task)
            self._stats.total_dead_lettered += 1
            return
        await self._require_current_generation()
        acknowledged = await ack_owned_stream_entry(
            self._redis,
            stream_key=stream_key,
            group=self._consumer_group,
            message_id=task.message_id,
            consumer_name=self._current_consumer_name(),
        )
        await self._settlement.require_acknowledged(stream_key, task.message_id, acknowledged)

    async def _move_to_dead_letter(self, source_stream: str, task: ReclaimedTask) -> None:
        await self._settlement.settle(source_stream, task)

    def _record_reclaim_stats(self, stream_key: str, count: int) -> None:
        self._stats.last_reclaim_time = datetime.now()
        self._stats.stream_stats[stream_key] = self._stats.stream_stats.get(stream_key, 0) + count

    def _legacy_consumer_name(self) -> str:
        return self._keys.consumer_name(self._worker_id)

    def _current_consumer_name(self) -> str:
        consumer_name = self._consumer_name_provider()
        if not consumer_name:
            raise RuntimeError("当前 task consumer name 为空")
        return consumer_name

    async def _is_current_generation(self) -> bool:
        if self._generation_guard is None:
            return True
        return bool(await self._generation_guard())

    async def _require_current_generation(self) -> None:
        if not await self._is_current_generation():
            raise RuntimeError("Pending task reclaimer lease generation 已失效")


def _last_delivery_time(idle_time_ms: int) -> datetime:
    return datetime.now() - timedelta(milliseconds=idle_time_ms)


def _task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and bool(task.cancelling())


__all__ = [
    "GlobalReclaimer",
    "PendingTaskReclaimer",
    "ReclaimConfig",
    "ReclaimedTask",
    "ReclaimStats",
    "cleanup_dead_consumers",
    "ensure_consumer_group",
]
