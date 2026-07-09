"""Cleanup for PostgreSQL task logs and Redis hot streams."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from loguru import logger
from tortoise import connections

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis import (
    get_redis_client,
    log_chunk_stream_pattern,
    log_stream_pattern,
)


@dataclass
class CleanupResult:
    postgres_rows_deleted: int = 0
    audit_rows_deleted: int = 0
    worker_event_rows_deleted: int = 0
    redis_streams_checked: int = 0
    redis_streams_trimmed: int = 0
    redis_streams_expired: int = 0
    errors: list[str] = field(default_factory=list)


class LogCleanupService:
    """Deletes expired PostgreSQL log rows and trims Redis hot streams."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._interval_hours = 24
        self._retention_days = getattr(settings, "TASK_LOG_RETENTION_DAYS", 30)
        # T7-B2a: audit_logs / worker_events 保留期
        self._audit_retention_days = getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90)
        self._worker_event_retention_days = getattr(
            settings, "WORKER_EVENT_RETENTION_DAYS", 30
        )
        self._batch_limit = getattr(settings, "LOG_CLEANUP_BATCH_LIMIT", 5000)
        self._redis_namespace = settings.REDIS_NAMESPACE
        self._log_stream_maxlen = settings.LOG_STREAM_MAXLEN
        self._log_stream_ttl_seconds = settings.LOG_STREAM_TTL_SECONDS
        self._log_chunk_stream_maxlen = settings.LOG_CHUNK_STREAM_MAXLEN
        self._log_chunk_ttl_seconds = settings.LOG_CHUNK_TTL_SECONDS

    async def start(self, interval_hours: int = 24):
        if self._running:
            return
        self._interval_hours = interval_hours
        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info("日志清理服务已启动: 间隔={}h", interval_hours)

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("日志清理服务已停止")

    async def cleanup_now(self) -> CleanupResult:
        return await self._do_cleanup()

    async def _cleanup_loop(self):
        await asyncio.sleep(300)
        while self._running:
            await self._do_cleanup()
            await asyncio.sleep(self._interval_hours * 3600)

    async def _do_cleanup(self) -> CleanupResult:
        result = CleanupResult()
        result.postgres_rows_deleted = await self._cleanup_postgres_logs()
        # T7-B2a (P0-2): audit_logs / worker_events 之前完全没被清
        result.audit_rows_deleted = await self._cleanup_table(
            table="audit_logs",
            time_column="created_at",
            retention_days=self._audit_retention_days,
        )
        result.worker_event_rows_deleted = await self._cleanup_table(
            table="worker_events",
            time_column="created_at",
            retention_days=self._worker_event_retention_days,
        )
        await self._cleanup_redis_streams(result)
        logger.info(
            "日志清理完成: pg_task_logs={}, audit_logs={}, worker_events={}, "
            "redis_checked={}, trimmed={}, expired={}",
            result.postgres_rows_deleted,
            result.audit_rows_deleted,
            result.worker_event_rows_deleted,
            result.redis_streams_checked,
            result.redis_streams_trimmed,
            result.redis_streams_expired,
        )
        return result

    async def _cleanup_postgres_logs(self) -> int:
        # T7-B2a: 改成批式循环 DELETE ... WHERE ... AND id IN (SELECT id ... LIMIT N)
        # 避免超大 DELETE 长事务锁表。
        return await self._cleanup_table(
            table="task_logs",
            time_column="timestamp",
            retention_days=self._retention_days,
        )

    async def _cleanup_table(
        self,
        *,
        table: str,
        time_column: str,
        retention_days: int,
    ) -> int:
        """按 `time_column < cutoff` 批式删过期行。

        单次 DELETE 最多 `LOG_CLEANUP_BATCH_LIMIT` 行；循环到没数据为止。
        表名与列名由调用方给出，均为受信来源（模块内部常量），无注入风险。
        """
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        conn = connections.get("default")
        total = 0
        while True:
            query = (
                f'DELETE FROM "{table}" '
                f'WHERE id IN ('
                f'  SELECT id FROM "{table}" WHERE "{time_column}" < $1 '
                f'  ORDER BY id LIMIT {int(self._batch_limit)}'
                f')'
            )
            deleted, _rows = await conn.execute_query(query, [cutoff])
            n = int(deleted or 0)
            total += n
            if n < self._batch_limit:
                break
            # 让出 event loop，避免连续大批量 DELETE 阻塞其他协程
            await asyncio.sleep(0.1)
        return total

    async def _cleanup_redis_streams(self, result: CleanupResult) -> None:
        redis = await get_redis_client()
        patterns = self._redis_patterns()
        for pattern, maxlen, ttl_seconds in patterns:
            async for key in redis.scan_iter(match=pattern, count=200):
                await self._trim_redis_stream(redis, key, maxlen, ttl_seconds, result)

    async def _trim_redis_stream(
        self,
        redis,
        key,
        maxlen: int,
        ttl_seconds: int,
        result: CleanupResult,
    ) -> None:
        result.redis_streams_checked += 1
        if maxlen > 0:
            await redis.xtrim(key, maxlen=maxlen, approximate=True)
            result.redis_streams_trimmed += 1
        if ttl_seconds > 0:
            await redis.expire(key, ttl_seconds)
            result.redis_streams_expired += 1

    def _redis_patterns(self) -> list[tuple[str, int, int]]:
        return [
            (
                log_stream_pattern(self._redis_namespace),
                self._log_stream_maxlen,
                self._log_stream_ttl_seconds,
            ),
            (
                log_chunk_stream_pattern(self._redis_namespace),
                self._log_chunk_stream_maxlen,
                self._log_chunk_ttl_seconds,
            ),
        ]


log_cleanup_service = LogCleanupService()
