"""日志清理服务

清理范围：
- 本地任务日志：settings.TASK_LOG_DIR
- 分布式日志：data/logs/distributed
"""

import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from src.core.config import settings
from src.services.logs.task_log_service import task_log_service


class LogCleanupService:
    def __init__(self):
        self._task = None
        self._running = False

    async def start(self, interval_hours = 24):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(interval_hours))

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self, interval_hours):
        while self._running:
            try:
                await self.cleanup()
            except Exception as e:
                logger.warning(f"日志清理失败: {e}")
            await asyncio.sleep(interval_hours * 3600)

    async def cleanup(self, retention_days = None):
        if retention_days is None:
            retention_days = settings.TASK_LOG_RETENTION_DAYS

        await task_log_service.cleanup_old_logs(retention_days=retention_days)
        await self._cleanup_distributed_logs(retention_days=retention_days)

    async def _cleanup_distributed_logs(self, retention_days):
        root = Path(settings.data_dir) / "logs" / "distributed"
        if not root.exists():
            return

        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).date()

        def cleanup_sync():
            deleted = 0
            for date_dir in root.iterdir():
                if not date_dir.is_dir():
                    continue
                try:
                    dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if dir_date < cutoff:
                    shutil.rmtree(date_dir, ignore_errors=True)
                    deleted += 1
            return deleted

        loop = asyncio.get_event_loop()
        deleted = await loop.run_in_executor(None, cleanup_sync)
        if deleted:
            logger.info(f"已清理 {deleted} 个分布式日志目录")


log_cleanup_service = LogCleanupService()
