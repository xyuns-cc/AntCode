"""临时文件自动清理服务"""

import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from src.core.config import settings


class TempCleanupService:
    """临时文件清理服务"""

    def __init__(self):
        self.temp_dir = os.path.join(settings.LOCAL_STORAGE_PATH, "temp")
        self.max_age_hours = 24
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def start_background_cleanup(self, interval_hours: int = 6):
        """启动后台清理"""
        if self._running:
            logger.warning("清理服务已运行")
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(interval_hours)
        )
        logger.info(f"清理服务已启动 [间隔{interval_hours}h]")

    async def stop_background_cleanup(self):
        """停止后台清理"""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        logger.info("清理服务已停止")

    async def _cleanup_loop(self, interval_hours: int):
        """清理循环"""
        while self._running:
            try:
                await self.cleanup_temp_files()
            except Exception as e:
                logger.error(f"清理失败: {e}")

            await asyncio.sleep(interval_hours * 3600)

    async def cleanup_temp_files(self, max_age_hours: Optional[int] = None) -> dict:
        """清理过期临时文件"""
        if max_age_hours is None:
            max_age_hours = self.max_age_hours

        if not os.path.exists(self.temp_dir):
            logger.debug(f"目录不存在: {self.temp_dir}")
            return {"cleaned": 0, "failed": 0, "total_size": 0}

        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        cleaned_count = 0
        failed_count = 0
        total_size = 0

        try:
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)

                if not os.path.isfile(file_path):
                    continue

                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))

                    if mtime < cutoff_time:
                        file_size = os.path.getsize(file_path)
                        os.remove(file_path)
                        cleaned_count += 1
                        total_size += file_size
                        logger.debug(f"已清理 {filename} ({file_size}字节)")
                except Exception as e:
                    logger.warning(f"清理失败 {filename}: {e}")
                    failed_count += 1

            if cleaned_count > 0:
                logger.info(
                    f"已清理{cleaned_count}个文件 "
                    f"释放{total_size / 1024 / 1024:.2f}MB"
                )

            return {
                "cleaned": cleaned_count,
                "failed": failed_count,
                "total_size": total_size
            }

        except Exception as e:
            logger.error(f"清理目录失败: {e}")
            return {"cleaned": 0, "failed": 0, "total_size": 0, "error": str(e)}

    async def get_temp_stats(self) -> dict:
        """获取目录统计"""
        if not os.path.exists(self.temp_dir):
            return {"exists": False, "file_count": 0, "total_size": 0}

        file_count = 0
        total_size = 0
        oldest_file = None
        newest_file = None

        try:
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)

                if not os.path.isfile(file_path):
                    continue

                file_count += 1
                total_size += os.path.getsize(file_path)

                mtime = os.path.getmtime(file_path)
                if oldest_file is None or mtime < oldest_file:
                    oldest_file = mtime
                if newest_file is None or mtime > newest_file:
                    newest_file = mtime

            return {
                "exists": True,
                "file_count": file_count,
                "total_size": total_size,
                "total_size_mb": round(total_size / 1024 / 1024, 2),
                "oldest_file": datetime.fromtimestamp(oldest_file).isoformat() if oldest_file else None,
                "newest_file": datetime.fromtimestamp(newest_file).isoformat() if newest_file else None,
            }

        except Exception as e:
            return {"exists": True, "error": str(e)}


temp_cleanup_service = TempCleanupService()

