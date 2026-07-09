"""Distributed worker log service backed by PostgreSQL."""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    postgres_task_log_service,
)
from antcode_core.application.services.workers.log_notifier import LogRealtimeNotifier

MAX_CACHE_LINES = 1000
LOG_TYPES = ("stdout", "stderr")


class DistributedLogService:
    """Persists distributed worker logs to PostgreSQL and pushes hot logs."""

    def __init__(self) -> None:
        self._log_cache: dict[str, list[str]] = defaultdict(list)
        self._task_status: dict[str, dict[str, Any]] = {}
        self._sequences: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._ws_queues: dict[str, asyncio.Queue[tuple[str, str]]] = {}
        self._ws_tasks: dict[str, asyncio.Task] = {}
        self._ws_idle_timeout = 1.0
        self._notifier: LogRealtimeNotifier | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.debug("分布式日志服务已启动")

    async def stop(self) -> None:
        self._running = False
        for task in self._ws_tasks.values():
            task.cancel()
        await self._drain_ws_tasks()
        self._ws_tasks.clear()
        self._ws_queues.clear()
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
        entries = await self._record_cache_and_entries(run_id, log_type, lines, timestamp)
        await postgres_task_log_service.append_entries(entries)
        if await self._has_ws_connections(run_id):
            self._enqueue_ws_logs(run_id, log_type, lines)

    async def update_task_status(
        self,
        run_id: str,
        status: str,
        exit_code: int | None = None,
        error_message: str | None = None,
        status_at: datetime | None = None,
    ) -> None:
        status_at = status_at or datetime.now(UTC)
        self._task_status[run_id] = {
            "status": status,
            "exit_code": exit_code,
            "error_message": error_message,
            "updated_at": status_at.isoformat(),
        }
        await self._update_runtime_status(run_id, status, exit_code, error_message, status_at)
        await self.append_log(run_id, "stdout", self._status_message(status, exit_code, error_message))
        await self._push_task_status(run_id)
        logger.info(f"分布式任务状态更新: {run_id} -> {status}")

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
        entries = await postgres_task_log_service.list_entries(
            run_id,
            log_type,
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
        for log_type in LOG_TYPES:
            self._log_cache.pop(self._cache_key(run_id, log_type), None)
            self._sequences.pop(self._cache_key(run_id, log_type), None)
        self._task_status.pop(run_id, None)
        task = self._ws_tasks.pop(run_id, None)
        if task and not task.done():
            task.cancel()
        self._ws_queues.pop(run_id, None)

    async def cleanup_old_logs(self, days: int = 7) -> None:
        raise RuntimeError("PostgreSQL log cleanup is handled by LogCleanupService")

    async def _record_cache_and_entries(
        self,
        run_id: str,
        log_type: str,
        lines: list[str],
        timestamp: datetime,
    ) -> list[PostgresLogEntry]:
        async with self._lock:
            key = self._cache_key(run_id, log_type)
            entries = self._entries_for_lines(run_id, log_type, lines, timestamp)
            cache = self._log_cache[key]
            cache.extend(lines)
            self._log_cache[key] = cache[-MAX_CACHE_LINES:]
            return entries

    def _entries_for_lines(
        self,
        run_id: str,
        log_type: str,
        lines: list[str],
        timestamp: datetime,
    ) -> list[PostgresLogEntry]:
        key = self._cache_key(run_id, log_type)
        entries = []
        for line in lines:
            self._sequences[key] += 1
            entries.append(
                PostgresLogEntry(
                    run_id=run_id,
                    log_type=log_type,
                    content=line,
                    sequence=self._sequences[key],
                    timestamp=timestamp,
                    level="ERROR" if log_type == "stderr" else "INFO",
                    source="worker_report",
                )
            )
        return entries

    async def _has_ws_connections(self, run_id: str) -> bool:
        if not self._notifier:
            return False
        return await self._notifier.has_connections(run_id)

    def _enqueue_ws_logs(self, run_id: str, log_type: str, lines: list[str]) -> None:
        queue = self._ws_queues.setdefault(run_id, asyncio.Queue())
        for line in lines:
            queue.put_nowait((log_type, line))
        if run_id not in self._ws_tasks:
            self._ws_tasks[run_id] = asyncio.create_task(self._ws_loop(run_id))

    async def _ws_loop(self, run_id: str) -> None:
        queue = self._ws_queues.get(run_id)
        if not queue:
            return
        try:
            while True:
                try:
                    log_type, content = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._ws_idle_timeout,
                    )
                except TimeoutError:
                    if queue.empty():
                        return
                    continue
                await self._push_log(run_id, log_type, content)
        finally:
            self._ws_tasks.pop(run_id, None)
            if queue.empty():
                self._ws_queues.pop(run_id, None)

    async def _push_log(self, run_id: str, log_type: str, content: str) -> None:
        if not self._notifier:
            return
        level = "ERROR" if log_type == "stderr" else "INFO"
        await self._notifier.send_log(run_id, log_type, content, level)

    async def _push_task_status(self, run_id: str) -> None:
        if not self._notifier:
            return
        status = await self._load_task_status(run_id)
        if not status:
            return
        await self._notifier.send_status(
            run_id=run_id,
            status=str(status["status"]).lower(),
            progress=self._progress_for_status(str(status["status"])),
            message=self._display_status_message(status),
        )

    async def _update_runtime_status(
        self,
        run_id: str,
        status: str,
        exit_code: int | None,
        error_message: str | None,
        status_at: datetime,
    ) -> None:
        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )

        await execution_status_service.update_runtime_status(
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
            "updated_at": execution.updated_at.isoformat(),
        }

    async def _drain_ws_tasks(self) -> None:
        if not self._ws_tasks:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*self._ws_tasks.values(), return_exceptions=True)

    def _format_lines(self, contents: list[str], timestamp: datetime) -> list[str]:
        formatted_at = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return [f"[{formatted_at}] {content}" for content in contents]

    def _status_message(
        self,
        status: str,
        exit_code: int | None,
        error_message: str | None,
    ) -> str:
        message = f"[STATUS] 任务状态更新: {status}"
        if exit_code is not None:
            message = f"{message}, 退出码: {exit_code}"
        if error_message:
            message = f"{message}, 错误: {error_message}"
        return message

    def _display_status_message(self, status: dict[str, Any]) -> str:
        value = str(status["status"]).lower()
        if value == "running":
            return "任务开始执行"
        if value == "success":
            return "任务执行成功"
        if value == "failed":
            return f"任务执行失败: {status.get('error_message') or '未知错误'}"
        if value == "timeout":
            return "任务执行超时"
        if value == "cancelled":
            return "任务已取消"
        return f"任务状态: {value}"

    def _progress_for_status(self, status: str) -> float | None:
        terminal = {"success", "failed", "timeout", "cancelled", "skipped", "rejected"}
        return 100.0 if status.lower() in terminal else None

    def _cache_key(self, run_id: str, log_type: str) -> str:
        return f"{run_id}:{log_type}"


distributed_log_service = DistributedLogService()
