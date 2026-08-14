"""Leader-only cleanup for expired provisional Worker registrations."""

from __future__ import annotations

import asyncio
import contextlib

from antcode_core.application.services.workers.registration_cleanup_service import (
    registration_cleanup_service,
)
from loguru import logger

from antcode_master.control.provisional_worker_cleanup import (
    settle_expired_provisional_worker_runs,
)
from antcode_master.leader import ensure_leader

REGISTRATION_CLEANUP_INTERVAL_SECONDS = 60.0


class WorkerRegistrationCleanupLoop:
    def __init__(self, interval_seconds: float = REGISTRATION_CLEANUP_INTERVAL_SECONDS) -> None:
        self._interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Worker 注册清理循环已启动: interval={}s", self._interval_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("Worker 注册清理循环已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                if await ensure_leader():
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Worker 注册清理循环异常")
            await asyncio.sleep(self._interval_seconds)

    async def _tick(self) -> None:
        result = await registration_cleanup_service.cleanup_expired(
            run_settler=settle_expired_provisional_worker_runs,
        )
        if result.expired_registrations or result.expired_pending_keys:
            logger.info(
                "Worker 注册清理完成: registrations={} workers={} pending_keys={}",
                result.expired_registrations,
                result.deleted_workers,
                result.expired_pending_keys,
            )


worker_registration_cleanup_loop = WorkerRegistrationCleanupLoop()

__all__ = ["WorkerRegistrationCleanupLoop", "worker_registration_cleanup_loop"]
