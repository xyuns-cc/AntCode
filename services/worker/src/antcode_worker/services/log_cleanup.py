"""
Worker 端日志清理服务

定时清理过期的本地任务日志，释放磁盘空间。
仅清理已完成传输（收到最终 ACK）的日志。

Requirements: 6.1
"""
import asyncio
import contextlib
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger


@dataclass
class CleanupResult:
    """清理结果"""
    directories_cleaned: int = 0
    files_cleaned: int = 0
    bytes_freed: int = 0
    run_ids: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped_incomplete: int = 0  # 跳过的未完成传输数


class LogCleanupService:
    """
    Worker 端日志清理服务

    定时清理过期的本地任务日志目录。
    仅清理满足以下条件的日志：
    1. 日志目录修改时间超过 retention_days 天
    2. 日志传输已完成（.meta 文件中 completed=true）

    日志目录结构: {logs_dir}/{run_id}/

    Requirements: 6.1
    """

    def __init__(
        self,
        logs_dir: str | Path,
        retention_days: int = 7,
        interval_hours: int = 24,
    ):
        """
        初始化日志清理服务

        Args:
            logs_dir: 日志存储目录
            retention_days: 日志保留天数（默认 7 天）
            interval_hours: 清理间隔（小时，默认 24 小时）
        """
        self._logs_dir = Path(logs_dir)
        self._retention_days = retention_days
        self._interval_hours = interval_hours
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def logs_dir(self) -> Path:
        """日志存储目录"""
        return self._logs_dir

    @property
    def retention_days(self) -> int:
        """日志保留天数"""
        return self._retention_days

    @property
    def interval_hours(self) -> int:
        """清理间隔（小时）"""
        return self._interval_hours

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    async def start(self) -> None:
        """
        启动日志清理服务

        Requirements: 6.1
        """
        if self._running:
            logger.warning("Worker 日志清理服务已在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            f"Worker 日志清理服务已启动: "
            f"logs_dir={self._logs_dir}, "
            f"retention_days={self._retention_days}, "
            f"interval_hours={self._interval_hours}"
        )

    async def stop(self) -> None:
        """停止日志清理服务"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        logger.info("Worker 日志清理服务已停止")

    async def _cleanup_loop(self) -> None:
        """清理循环"""
        # 启动后延迟执行首次清理（避免启动时负载过高）
        await asyncio.sleep(300)  # 5 分钟后首次执行

        while self._running:
            try:
                result = await self.cleanup_now()
                if result.directories_cleaned > 0:
                    logger.info(
                        f"Worker 日志清理完成: "
                        f"cleaned={result.directories_cleaned}, "
                        f"freed={self._format_bytes(result.bytes_freed)}, "
                        f"skipped_incomplete={result.skipped_incomplete}"
                    )
            except Exception as e:
                logger.error(f"Worker 日志清理失败: {e}")

            # 等待下次清理
            await asyncio.sleep(self._interval_hours * 3600)

    async def cleanup_now(self) -> CleanupResult:
        """
        立即执行清理

        Returns:
            清理结果

        Requirements: 6.1
        """
        result = CleanupResult()

        if not self._logs_dir.exists():
            return result

        cutoff_time = datetime.now(UTC) - timedelta(days=self._retention_days)
        cutoff_timestamp = cutoff_time.timestamp()

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._sync_cleanup_logs,
                cutoff_timestamp,
            )
        except Exception as e:
            logger.error(f"Worker 日志清理失败: {e}")
            result.errors.append(str(e))

        return result

    def _sync_cleanup_logs(self, cutoff_timestamp: float) -> CleanupResult:
        """
        同步清理日志（在线程池中执行）

        Args:
            cutoff_timestamp: 截止时间戳

        Returns:
            清理结果

        Requirements: 6.1
        """
        result = CleanupResult()

        try:
            for entry in os.scandir(self._logs_dir):
                if not entry.is_dir():
                    continue
                if entry.name in {"spool", "wal"}:
                    continue

                run_id = entry.name

                try:
                    # 检查目录修改时间
                    mtime = entry.stat().st_mtime
                    if mtime >= cutoff_timestamp:
                        # 未过期，跳过
                        continue

                    # 检查传输是否完成
                    if not self._is_transfer_completed(entry.path):
                        result.skipped_incomplete += 1
                        logger.debug(
                            f"跳过未完成传输的日志目录: run_id={run_id}"
                        )
                        continue

                    # 计算目录大小
                    dir_size = self._get_dir_size(entry.path)

                    # 删除目录
                    shutil.rmtree(entry.path)

                    result.directories_cleaned += 1
                    result.bytes_freed += dir_size
                    result.run_ids.append(run_id)

                    # 记录清理日志（Requirements: 6.1）
                    logger.info(
                        f"清理过期 Worker 日志目录: "
                        f"run_id={run_id}, "
                        f"freed={self._format_bytes(dir_size)}"
                    )

                except OSError as e:
                    logger.warning(f"清理目录失败 {entry.path}: {e}")
                    result.errors.append(f"{run_id}: {e}")

        except OSError as e:
            logger.error(f"扫描日志目录失败: {e}")
            result.errors.append(str(e))

        return result

    def _is_transfer_completed(self, log_dir: str) -> bool:
        """
        检查日志传输是否完成

        通过检查 .meta 文件中的 completed 字段判断。
        如果 stdout.meta 和 stderr.meta 都存在且都标记为 completed，
        则认为传输完成。

        Args:
            log_dir: 日志目录路径

        Returns:
            是否传输完成
        """
        log_path = Path(log_dir)

        # 检查 stdout.meta
        stdout_meta_path = log_path / "stdout.meta"
        if stdout_meta_path.exists() and not self._check_meta_completed(stdout_meta_path):
            return False

        # 检查 stderr.meta
        stderr_meta_path = log_path / "stderr.meta"
        if stderr_meta_path.exists() and not self._check_meta_completed(stderr_meta_path):
            return False

        # 如果没有 .meta 文件，检查目录是否为空或只有日志文件
        # 这种情况可能是旧格式的日志，允许清理
        if not stdout_meta_path.exists() and not stderr_meta_path.exists():
            # 没有 meta 文件，可能是旧格式或空目录，允许清理
            return True

        return True

    def _check_meta_completed(self, meta_path: Path) -> bool:
        """检查 meta 文件中的 completed 字段"""
        try:
            import json
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            return meta.get("completed", False)
        except Exception:
            # 解析失败，假设未完成
            return False

    def _get_dir_size(self, path: str) -> int:
        """
        计算目录大小

        Args:
            path: 目录路径

        Returns:
            目录大小（字节）
        """
        total_size = 0
        try:
            for dirpath, _dirnames, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    with contextlib.suppress(OSError):
                        total_size += os.path.getsize(filepath)
        except OSError:
            pass
        return total_size

    @staticmethod
    def _format_bytes(size: int) -> str:
        """
        格式化字节大小

        Args:
            size: 字节大小

        Returns:
            格式化后的字符串
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}PB"

    async def cleanup_execution(self, run_id: str, force: bool = False) -> dict:
        """
        清理指定执行 ID 的日志

        Args:
            run_id: 执行 ID
            force: 是否强制清理（忽略传输完成状态）

        Returns:
            清理结果字典
        """
        result = {
            "run_id": run_id,
            "cleaned": False,
            "bytes_freed": 0,
            "error": None,
        }

        log_dir = self._logs_dir / run_id
        if not log_dir.exists():
            result["error"] = "目录不存在"
            return result

        # 检查传输是否完成（除非强制清理）
        if not force and not self._is_transfer_completed(str(log_dir)):
            result["error"] = "传输未完成"
            return result

        try:
            dir_size = self._get_dir_size(str(log_dir))
            shutil.rmtree(log_dir)
            result["cleaned"] = True
            result["bytes_freed"] = dir_size
            result["bytes_freed_formatted"] = self._format_bytes(dir_size)

            logger.info(
                f"清理 Worker 日志目录: "
                f"run_id={run_id}, "
                f"freed={self._format_bytes(dir_size)}"
            )
        except OSError as e:
            result["error"] = str(e)
            logger.error(f"清理日志目录失败 {log_dir}: {e}")

        return result

    def get_status(self) -> dict:
        """
        获取服务状态

        Returns:
            状态信息字典
        """
        return {
            "running": self._running,
            "logs_dir": str(self._logs_dir),
            "retention_days": self._retention_days,
            "interval_hours": self._interval_hours,
        }
