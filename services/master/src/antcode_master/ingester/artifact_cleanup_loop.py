"""Leader-gated wrapper around ArtifactCleanupService."""

from __future__ import annotations

import asyncio
import contextlib

from antcode_core.application.services.projects.artifact_cleanup_service import (
    artifact_cleanup_service,
)
from loguru import logger

from antcode_master.leader import ensure_leader


class ArtifactCleanupLoop:
    """Runs artifact cleanup exclusively on the leader Master."""

    def __init__(
        self,
        interval_hours: int = 24,
        leader_poll_interval: float = 30.0,
    ) -> None:
        self._interval_hours = interval_hours
        self._leader_poll_interval = leader_poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("artifact 清理循环已启动: 间隔={}h", self._interval_hours)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("artifact 清理循环已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                if not await ensure_leader():
                    await asyncio.sleep(self._leader_poll_interval)
                    continue
                await artifact_cleanup_service.cleanup_now()
                await asyncio.sleep(self._interval_hours * 3600)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("artifact 清理循环异常: {}", exc)
                await asyncio.sleep(self._leader_poll_interval)


artifact_cleanup_loop = ArtifactCleanupLoop()

__all__ = ["ArtifactCleanupLoop", "artifact_cleanup_loop"]
