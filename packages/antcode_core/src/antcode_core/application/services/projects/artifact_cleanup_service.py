"""Reference-aware TTL cleanup for PostgreSQL source bundle artifacts.

Removes `source_artifact_chunks` + `source_artifacts` rows older than the
configured retention window, but only when no `run_source_snapshots` row
created within the same window still references the artifact.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from loguru import logger
from tortoise import connections
from tortoise.transactions import in_transaction

from antcode_core.common.config import settings


@dataclass
class ArtifactCleanupResult:
    artifacts_deleted: int = 0
    chunks_deleted: int = 0
    cutoff: datetime | None = None
    errors: list[str] = field(default_factory=list)


class ArtifactCleanupService:
    """Deletes expired source bundle artifacts that are no longer referenced."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._interval_hours = 24
        self._retention_days = getattr(settings, "ARTIFACT_RETENTION_DAYS", 30)
        self._initial_delay_seconds = 300

    async def start(self, interval_hours: int = 24) -> None:
        if self._running:
            return
        self._interval_hours = interval_hours
        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            "artifact 清理服务已启动: 间隔={}h retention={}d",
            interval_hours,
            self._retention_days,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("artifact 清理服务已停止")

    async def cleanup_now(self) -> ArtifactCleanupResult:
        return await self._do_cleanup()

    async def _cleanup_loop(self) -> None:
        await asyncio.sleep(self._initial_delay_seconds)
        while self._running:
            try:
                await self._do_cleanup()
            except Exception as exc:
                logger.error("artifact 清理任务异常: {}", exc)
            await asyncio.sleep(self._interval_hours * 3600)

    async def _do_cleanup(self) -> ArtifactCleanupResult:
        result = ArtifactCleanupResult()
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        result.cutoff = cutoff
        conn = connections.get("default")
        async with in_transaction("default"):
            chunk_sql = (
                "DELETE FROM source_artifact_chunks "
                "WHERE artifact_id IN ("
                "  SELECT id FROM source_artifacts "
                "   WHERE created_at < $1 "
                "     AND id NOT IN ("
                "       SELECT DISTINCT artifact_id FROM run_source_snapshots "
                "        WHERE created_at >= $1"
                "     )"
                ")"
            )
            chunks_deleted, _ = await conn.execute_query(chunk_sql, [cutoff])
            artifact_sql = (
                "DELETE FROM source_artifacts "
                "WHERE created_at < $1 "
                "  AND id NOT IN ("
                "    SELECT DISTINCT artifact_id FROM run_source_snapshots "
                "     WHERE created_at >= $1"
                "  )"
            )
            artifacts_deleted, _ = await conn.execute_query(artifact_sql, [cutoff])
        result.chunks_deleted = int(chunks_deleted or 0)
        result.artifacts_deleted = int(artifacts_deleted or 0)
        logger.info(
            "artifact 清理完成: cutoff={} artifacts={} chunks={}",
            cutoff.isoformat(),
            result.artifacts_deleted,
            result.chunks_deleted,
        )
        return result


artifact_cleanup_service = ArtifactCleanupService()
