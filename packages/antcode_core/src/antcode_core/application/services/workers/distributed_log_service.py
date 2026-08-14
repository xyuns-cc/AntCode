"""Distributed worker log service backed by PostgreSQL."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.application.services.logs.log_sequence_allocator import (
    LogSequenceAllocator,
    redis_log_sequence_allocator,
)
from antcode_core.application.services.logs.postgres_log_sequence_query import (
    list_persisted_log_sequences,
)
from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry, postgres_task_log_service
from antcode_core.application.services.workers.distributed_log_entries import entries_for_lines
from antcode_core.application.services.workers.distributed_log_status import (
    TERMINAL_STATUSES,
    display_status_message,
    status_log_message,
    status_progress,
)
from antcode_core.application.services.workers.log_notifier import LogRealtimeNotifier
from antcode_core.common.error_messages import normalize_persisted_error_message

MAX_CACHE_LINES = 1000
MAX_PUSH_QUEUE_LINES = 1000
LOG_TYPES = ("stdout", "stderr")
CACHE_TTL_SECONDS = 6 * 60 * 60
SWEEP_INTERVAL_SECONDS = 60.0


def _monotonic() -> float:
    return time.monotonic()


class DistributedLogService:
    """Persists distributed worker logs to PostgreSQL and pushes hot logs."""

    def __init__(self, sequence_allocator: LogSequenceAllocator) -> None:
        self._sequence_allocator = sequence_allocator
        self._log_cache: dict[str, list[str]] = defaultdict(list)
        self._task_status: dict[str, dict[str, Any]] = {}
        self._last_touch: dict[str, float] = {}
        self._last_sweep = 0.0
        self._lock = asyncio.Lock()
        self._push_queues: dict[str, asyncio.Queue[PostgresLogEntry]] = {}
        self._push_tasks: dict[str, asyncio.Task] = {}
        self._push_idle_timeout = 1.0
        self._notifier: LogRealtimeNotifier | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.debug("分布式日志服务已启动")

    async def stop(self) -> None:
        self._running = False
        for task in self._push_tasks.values():
            task.cancel()
        await self._drain_push_tasks()
        self._push_tasks.clear()
        self._push_queues.clear()
        logger.debug("分布式日志服务已停止")

    def set_notifier(self, notifier: LogRealtimeNotifier | None) -> None:
        self._notifier = notifier

    async def append_log(self, run_id: str, log_type: str, content: str) -> None:
        await self.append_logs(run_id=run_id, log_type=log_type, contents=[content])

    async def append_logs(
        self,
        run_id: str,
        log_type: str,
        contents: list[str],
    ) -> None:
        if not contents:
            return
        timestamp = datetime.now(UTC)
        lines = self._format_lines(contents, timestamp)
        entries = await self._persist_and_cache(
            run_id,
            log_type,
            lines,
            timestamp=timestamp,
        )
        if not await self._has_stream_connections(run_id):
            return
        persisted = await list_persisted_log_sequences(entries)
        await self._enqueue_push_logs(run_id, persisted)

    async def update_task_status(
        self,
        run_id: str,
        status: str,
        *,
        exit_code: int | None = None,
        error_message: str | None = None,
        status_at: datetime | None = None,
    ) -> bool:
        status_at = status_at or datetime.now(UTC)
        error_message = normalize_persisted_error_message(error_message)
        updated = await self._update_runtime_status(
            run_id,
            status,
            exit_code=exit_code,
            error_message=error_message,
            status_at=status_at,
        )
        if not updated:
            return False
        self._task_status[run_id] = {
            "status": status,
            "exit_code": exit_code,
            "error_message": error_message,
            "updated_at": status_at.isoformat(),
        }
        await self.append_log(run_id, "stdout", status_log_message(status, exit_code, error_message))
        await self._push_task_status(run_id)
        if status.lower() in TERMINAL_STATUSES:
            self._clear_hot_cache(run_id)
        logger.info(f"分布式任务状态更新: {run_id} -> {status}")
        return True

    async def get_logs(
        self,
        run_id: str,
        log_type: str = "stdout",
        tail: int | None = 100,
    ) -> list[str]:
        cache_key = self._cache_key(run_id, log_type)
        cache = self._log_cache.get(cache_key, [])
        if tail is not None and len(cache) >= tail:
            return cache[-tail:]
        # postgres_task_log_service.list_entries 的 log_type 是 keyword-only，
        # 位置参数会抛 TypeError；必须以 log_type=... 传参。
        entries = await postgres_task_log_service.list_entries(
            run_id,
            log_type=log_type,
            limit=max(tail or MAX_CACHE_LINES, MAX_CACHE_LINES),
        )
        lines = [entry.content for entry in entries]
        return lines[-tail:] if tail is not None else lines

    async def get_task_status(self, run_id: str) -> dict[str, Any] | None:
        if run_id in self._task_status:
            return self._task_status[run_id]
        return await self._load_task_status(run_id)

    async def get_all_logs(self, run_id: str) -> dict[str, list[str]]:
        return {log_type: await self.get_logs(run_id, log_type, 5000) for log_type in LOG_TYPES}

    def clear_cache(self, run_id: str) -> None:
        self._clear_hot_cache(run_id)
        task = self._push_tasks.pop(run_id, None)
        if task and not task.done():
            task.cancel()
        self._push_queues.pop(run_id, None)

    def _clear_hot_cache(self, run_id: str) -> None:
        for log_type in LOG_TYPES:
            self._log_cache.pop(self._cache_key(run_id, log_type), None)
        self._task_status.pop(run_id, None)
        self._last_touch.pop(run_id, None)

    async def cleanup_old_logs(self, days: int = 7) -> None:
        raise RuntimeError("PostgreSQL log cleanup is handled by LogCleanupService")

    async def _persist_and_cache(
        self,
        run_id: str,
        log_type: str,
        lines: list[str],
        *,
        timestamp: datetime,
    ) -> list[PostgresLogEntry]:
        """L4：序列分配 → PG 落库 → 缓存追加作为同锁原子单元执行。

        (a) 缓存只在 PG append 成功后追加：append 抛错时不会留下未落库的
            "幽灵"缓存行，重试也不会与旧缓存行重复。
        (b) allocate 与 cache.extend 同处一把锁内串行：并发同
            ``(run_id, log_type)`` append 的分配顺序即缓存顺序，杜绝原始字符串
            缓存与序列号错位导致的 SSE 快照倒序。
        锁跨 PG await 会串行化全部 append（吞吐权衡）；分配器 / PG 服务都不
        回收本锁，无重入死锁。残留：append 失败时已分配的序列号被消耗，重试
        分配更高区间会在 PG 留下序列空洞——序列仍严格单调，仅不连续，不影响
        排序 / 去重；彻底消除需分配回滚或改用 PG 自增序列（更大重构）。
        """
        async with self._lock:
            sequences = await self._sequence_allocator.allocate(run_id, log_type, len(lines))
            entries = entries_for_lines(
                run_id,
                log_type,
                lines,
                sequences=sequences,
                timestamp=timestamp,
            )
            written = await postgres_task_log_service.append_entries(entries)
            if written != len(entries):
                raise RuntimeError(f"日志写入数量异常: requested={len(entries)} written={written}")
            key = self._cache_key(run_id, log_type)
            cache = self._log_cache[key]
            cache.extend(lines)
            self._log_cache[key] = cache[-MAX_CACHE_LINES:]
            self._touch_and_sweep(run_id, _monotonic())
        return entries

    def _touch_and_sweep(self, run_id: str, now: float) -> None:
        """记录 run_id 最后触碰时间并惰性清扫超期条目（须在 ``self._lock`` 内调用）。"""
        self._last_touch[run_id] = now
        if now - self._last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        expired = [rid for rid, touched in self._last_touch.items() if now - touched > CACHE_TTL_SECONDS]
        for rid in expired:
            self._clear_hot_cache(rid)

    async def _has_stream_connections(self, run_id: str) -> bool:
        if not self._notifier:
            return False
        return await self._notifier.has_connections(run_id)

    async def _enqueue_push_logs(self, run_id: str, entries: list[PostgresLogEntry]) -> None:
        queue = self._push_queues.setdefault(
            run_id,
            asyncio.Queue(maxsize=MAX_PUSH_QUEUE_LINES),
        )
        task = self._push_tasks.get(run_id)
        if task is None or task.done():
            self._push_tasks[run_id] = asyncio.create_task(self._push_loop(run_id))
        for entry in entries:
            await queue.put(entry)

    async def _push_loop(self, run_id: str) -> None:
        queue = self._push_queues.get(run_id)
        if not queue:
            return
        try:
            while True:
                try:
                    entry = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._push_idle_timeout,
                    )
                except TimeoutError:
                    if queue.empty():
                        return
                    continue
                await self._push_log(entry)
        finally:
            self._push_tasks.pop(run_id, None)
            if queue.empty():
                self._push_queues.pop(run_id, None)

    async def _push_log(self, entry: PostgresLogEntry) -> None:
        if not self._notifier:
            return
        await self._notifier.send_log(
            run_id=entry.run_id,
            log_type=entry.log_type,
            content=entry.content,
            level=entry.level,
            sequence=entry.sequence,
            storage_id=entry.storage_id,
        )

    async def _push_task_status(self, run_id: str) -> None:
        if not self._notifier:
            return
        status = await self._load_task_status(run_id)
        if not status:
            return
        await self._notifier.send_status(
            run_id=run_id,
            status=str(status["status"]).lower(),
            progress=status_progress(str(status["status"])),
            message=display_status_message(status),
        )

    async def _update_runtime_status(
        self,
        run_id: str,
        status: str,
        *,
        exit_code: int | None,
        error_message: str | None,
        status_at: datetime,
    ) -> bool:
        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )

        return await execution_status_service.update_runtime_status(
            run_id=run_id,
            status=status,
            status_at=status_at,
            exit_code=exit_code,
            error_message=error_message,
        )

    async def _load_task_status(self, run_id: str) -> dict[str, Any] | None:
        from antcode_core.domain.models.task_run import TaskRun

        execution = await TaskRun.get_or_none(run_id=run_id)
        if not execution:
            return None
        return {
            "status": execution.status.value,
            "exit_code": execution.exit_code,
            "error_message": execution.error_message,
            "updated_at": (
                execution.runtime_updated_at or execution.dispatch_updated_at or execution.created_at
            ).isoformat(),
        }

    async def _drain_push_tasks(self) -> None:
        if not self._push_tasks:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*self._push_tasks.values(), return_exceptions=True)

    def _format_lines(self, contents: list[str], timestamp: datetime) -> list[str]:
        formatted_at = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return [f"[{formatted_at}] {content}" for content in contents]

    def _cache_key(self, run_id: str, log_type: str) -> str:
        return f"{run_id}:{log_type}"


distributed_log_service = DistributedLogService(redis_log_sequence_allocator)
