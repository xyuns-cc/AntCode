"""
日志清理服务

定时清理过期的任务日志和分布式日志

Requirements: 6.2, 6.3
"""
import asyncio
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis import get_redis_client
from antcode_core.infrastructure.storage import (
    LocalFileStorageBackend,
    S3FileStorageBackend,
    get_file_storage_backend,
)


@dataclass
class CleanupResult:
    """清理结果"""
    directories_cleaned: int = 0
    files_cleaned: int = 0
    bytes_freed: int = 0
    execution_ids: list = None
    errors: list = None
    
    def __post_init__(self):
        if self.execution_ids is None:
            self.execution_ids = []
        if self.errors is None:
            self.errors = []


@dataclass
class RedisCleanupResult:
    """Redis 清理结果"""
    streams_checked: int = 0
    streams_trimmed: int = 0
    streams_expired: int = 0
    errors: list = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


@dataclass
class StorageCleanupResult:
    """对象存储清理结果"""
    files_deleted: int = 0
    bytes_freed: int = 0
    errors: list = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class LogCleanupService:
    """
    日志清理服务
    
    定时清理过期的任务日志目录，包括：
    - 日志双通道架构的日志（data/logs/tasks/{execution_id}/）
    - 分布式日志（data/logs/distributed/{date}/{execution_id}/）
    - 本地任务日志（data/logs/tasks/{date}/{execution_id}/）
    
    Requirements: 6.2, 6.3
    """
    
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # 清理间隔（小时）
        self._interval_hours = 24
        # 日志保留天数
        self._retention_days = getattr(settings, 'TASK_LOG_RETENTION_DAYS', 30)
        # 日志存储路径
        self._task_log_dir = Path(settings.TASK_LOG_DIR)
        self._distributed_log_dir = Path(settings.data_dir) / "logs" / "distributed"
        # Redis / object storage retention
        self._redis_namespace = settings.REDIS_NAMESPACE
        self._log_stream_maxlen = settings.LOG_STREAM_MAXLEN
        self._log_stream_ttl_seconds = settings.LOG_STREAM_TTL_SECONDS
        self._log_chunk_stream_maxlen = settings.LOG_CHUNK_STREAM_MAXLEN
        self._log_chunk_ttl_seconds = settings.LOG_CHUNK_TTL_SECONDS
        self._log_archive_prefix = settings.LOG_ARCHIVE_PREFIX
        self._log_archive_retention_days = settings.LOG_ARCHIVE_RETENTION_DAYS
    
    async def start(self, interval_hours: int = 24):
        """
        启动日志清理服务
        
        Args:
            interval_hours: 清理间隔（小时），默认 24 小时
        """
        if self._running:
            return
        
        self._interval_hours = interval_hours
        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            f"日志清理服务已启动: 间隔={interval_hours}h, "
            f"保留={self._retention_days}天, "
            f"任务日志目录={self._task_log_dir}, "
            f"分布式日志目录={self._distributed_log_dir}"
        )
    
    async def stop(self):
        """停止日志清理服务"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("日志清理服务已停止")
    
    async def _cleanup_loop(self):
        """清理循环"""
        # 启动后延迟执行首次清理（避免启动时负载过高）
        await asyncio.sleep(300)  # 5 分钟后首次执行
        
        while self._running:
            try:
                await self._do_cleanup()
            except Exception as e:
                logger.error(f"日志清理失败: {e}")
            
            # 等待下次清理
            await asyncio.sleep(self._interval_hours * 3600)
    
    async def _do_cleanup(self):
        """执行清理"""
        start_time = datetime.now()
        logger.info(f"开始清理过期日志（保留 {self._retention_days} 天）...")
        
        # 1. 清理日志双通道架构的日志（按 execution_id 组织）
        dual_channel_result = await self._cleanup_dual_channel_logs()

        # 2. 清理分布式日志（按日期组织）
        distributed_result = await self._cleanup_distributed_logs()

        # 3. 清理本地任务日志（按日期组织）
        local_result = await self._cleanup_local_logs()

        # 4. 清理 Redis 日志流
        redis_result = await self._cleanup_redis_streams()

        # 5. 清理对象存储归档日志
        storage_result = await self._cleanup_log_archives()
        
        duration = (datetime.now() - start_time).total_seconds()
        
        total_dirs = (
            dual_channel_result.directories_cleaned +
            distributed_result.directories_cleaned +
            local_result.directories_cleaned
        )
        total_bytes = (
            dual_channel_result.bytes_freed +
            distributed_result.bytes_freed +
            local_result.bytes_freed
        )
        
        # 记录清理日志（Requirements: 6.3）
        logger.info(
            f"日志清理完成: "
            f"双通道日志={dual_channel_result.directories_cleaned}个目录, "
            f"分布式日志={distributed_result.directories_cleaned}个目录, "
            f"本地日志={local_result.directories_cleaned}个目录, "
            f"RedisStreams(checked={redis_result.streams_checked}, "
            f"trimmed={redis_result.streams_trimmed}, "
            f"expired={redis_result.streams_expired}), "
            f"归档日志={storage_result.files_deleted}个文件, "
            f"总计释放={self._format_bytes(total_bytes)}, "
            f"耗时={duration:.1f}s"
        )
        
        # 记录清理的 execution_id（Requirements: 6.3）
        all_execution_ids = (
            dual_channel_result.execution_ids +
            distributed_result.execution_ids
        )
        if all_execution_ids:
            logger.debug(
                f"清理的 execution_id 列表: {all_execution_ids[:20]}"
                f"{'...' if len(all_execution_ids) > 20 else ''}"
            )

    async def _cleanup_redis_streams(self) -> RedisCleanupResult:
        """清理 Redis 日志流（MAXLEN + TTL）"""
        result = RedisCleanupResult()
        if not settings.REDIS_ENABLED:
            return result

        try:
            redis = await get_redis_client()
        except Exception as e:
            logger.warning(f"Redis 不可用，跳过日志流清理: {e}")
            result.errors.append(str(e))
            return result

        patterns = [
            (
                f"{self._redis_namespace}:log:stream:*",
                self._log_stream_maxlen,
                self._log_stream_ttl_seconds,
            ),
            (
                f"{self._redis_namespace}:log:chunk:*",
                self._log_chunk_stream_maxlen,
                self._log_chunk_ttl_seconds,
            ),
        ]

        for pattern, maxlen, ttl_seconds in patterns:
            async for key in redis.scan_iter(match=pattern, count=200):
                result.streams_checked += 1
                try:
                    if maxlen > 0:
                        await redis.xtrim(key, maxlen=maxlen, approximate=True)
                        result.streams_trimmed += 1
                    if ttl_seconds > 0:
                        await redis.expire(key, ttl_seconds)
                        result.streams_expired += 1
                except Exception as e:
                    key_name = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)
                    result.errors.append(f"{key_name}: {e}")

        return result

    async def _cleanup_log_archives(self) -> StorageCleanupResult:
        """清理对象存储中的日志归档（best-effort）"""
        result = StorageCleanupResult()
        if self._log_archive_retention_days <= 0:
            return result

        try:
            backend = get_file_storage_backend()
        except Exception as e:
            logger.warning(f"存储后端不可用，跳过归档清理: {e}")
            result.errors.append(str(e))
            return result

        prefix = (self._log_archive_prefix or "").strip("/")
        if not prefix:
            return result

        cutoff_time = datetime.now(timezone.utc) - timedelta(days=self._log_archive_retention_days)

        if isinstance(backend, LocalFileStorageBackend):
            archive_root = Path(backend.storage_root) / prefix
            if not archive_root.exists():
                return result
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._sync_cleanup_log_archive_dir,
                archive_root,
                cutoff_time,
            )

        if isinstance(backend, S3FileStorageBackend):
            await self._cleanup_s3_log_archives(
                backend=backend,
                prefix=prefix,
                cutoff_time=cutoff_time,
                result=result,
            )
            return result

        return result

    def _sync_cleanup_log_archive_dir(
        self,
        archive_root: Path,
        cutoff_time: datetime,
    ) -> StorageCleanupResult:
        """同步清理本地归档目录"""
        result = StorageCleanupResult()
        cutoff_timestamp = cutoff_time.timestamp()

        try:
            for file_path in archive_root.rglob("*"):
                if not file_path.is_file():
                    continue
                try:
                    if file_path.stat().st_mtime < cutoff_timestamp:
                        size = file_path.stat().st_size
                        file_path.unlink()
                        result.files_deleted += 1
                        result.bytes_freed += size
                except OSError as e:
                    result.errors.append(f"{file_path}: {e}")
        except OSError as e:
            result.errors.append(str(e))

        return result

    async def _cleanup_s3_log_archives(
        self,
        backend: S3FileStorageBackend,
        prefix: str,
        cutoff_time: datetime,
        result: StorageCleanupResult,
    ) -> None:
        """清理 S3/MinIO 中的归档日志"""
        try:
            client = await backend._get_client()
        except Exception as e:
            logger.warning(f"S3 客户端不可用，跳过归档清理: {e}")
            result.errors.append(str(e))
            return

        continuation_token = None
        prefix_key = f"{prefix}/"

        while True:
            kwargs = {"Bucket": backend.bucket, "Prefix": prefix_key}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            response = await client.list_objects_v2(**kwargs)
            contents = response.get("Contents", []) or []

            for obj in contents:
                last_modified = obj.get("LastModified")
                if last_modified is None:
                    continue
                if last_modified.tzinfo is None:
                    last_modified = last_modified.replace(tzinfo=timezone.utc)
                if last_modified < cutoff_time:
                    key = obj.get("Key")
                    if not key:
                        continue
                    await client.delete_object(Bucket=backend.bucket, Key=key)
                    result.files_deleted += 1
                    result.bytes_freed += obj.get("Size", 0) or 0

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
    
    async def _cleanup_dual_channel_logs(self) -> CleanupResult:
        """
        清理日志双通道架构的日志
        
        日志目录结构: data/logs/tasks/{execution_id}/
        按目录修改时间判断是否过期
        
        Requirements: 6.2
        """
        result = CleanupResult()
        
        if not self._task_log_dir.exists():
            return result
        
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        cutoff_timestamp = cutoff_time.timestamp()
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._sync_cleanup_dual_channel_logs,
                cutoff_timestamp,
            )
        except Exception as e:
            logger.error(f"清理双通道日志失败: {e}")
            result.errors.append(str(e))
        
        return result
    
    def _sync_cleanup_dual_channel_logs(self, cutoff_timestamp: float) -> CleanupResult:
        """
        同步清理双通道日志（在线程池中执行）
        
        Args:
            cutoff_timestamp: 截止时间戳
            
        Returns:
            清理结果
        """
        result = CleanupResult()
        
        try:
            for entry in os.scandir(self._task_log_dir):
                if not entry.is_dir():
                    continue
                
                # 检查是否为日期目录（YYYY-MM-DD 格式）
                # 如果是日期目录，跳过（由 _cleanup_local_logs 处理）
                try:
                    datetime.strptime(entry.name, "%Y-%m-%d")
                    continue  # 是日期目录，跳过
                except ValueError:
                    pass  # 不是日期目录，继续处理
                
                # 检查目录修改时间
                try:
                    mtime = entry.stat().st_mtime
                    if mtime < cutoff_timestamp:
                        # 计算目录大小
                        dir_size = self._get_dir_size(entry.path)
                        
                        # 删除目录
                        shutil.rmtree(entry.path)
                        
                        result.directories_cleaned += 1
                        result.bytes_freed += dir_size
                        result.execution_ids.append(entry.name)
                        
                        # 记录清理日志（Requirements: 6.3）
                        logger.info(
                            f"清理过期日志目录: execution_id={entry.name}, "
                            f"释放空间={self._format_bytes(dir_size)}"
                        )
                except OSError as e:
                    logger.warning(f"清理目录失败 {entry.path}: {e}")
                    result.errors.append(f"{entry.name}: {e}")
        except OSError as e:
            logger.error(f"扫描日志目录失败: {e}")
            result.errors.append(str(e))
        
        return result
    
    async def _cleanup_distributed_logs(self) -> CleanupResult:
        """
        清理分布式日志
        
        日志目录结构: data/logs/distributed/{date}/{execution_id}/
        按日期目录名判断是否过期
        
        Requirements: 6.2
        """
        result = CleanupResult()
        
        if not self._distributed_log_dir.exists():
            return result
        
        cutoff_date = datetime.now() - timedelta(days=self._retention_days)
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._sync_cleanup_distributed_logs,
                cutoff_date,
            )
        except Exception as e:
            logger.error(f"清理分布式日志失败: {e}")
            result.errors.append(str(e))
        
        return result
    
    def _sync_cleanup_distributed_logs(self, cutoff_date: datetime) -> CleanupResult:
        """
        同步清理分布式日志（在线程池中执行）
        
        Args:
            cutoff_date: 截止日期
            
        Returns:
            清理结果
        """
        result = CleanupResult()
        
        try:
            for date_entry in os.scandir(self._distributed_log_dir):
                if not date_entry.is_dir():
                    continue
                
                # 解析日期目录名
                try:
                    dir_date = datetime.strptime(date_entry.name, "%Y-%m-%d")
                    if dir_date < cutoff_date:
                        # 收集该日期目录下的所有 execution_id
                        execution_ids = []
                        try:
                            for exec_entry in os.scandir(date_entry.path):
                                if exec_entry.is_dir():
                                    execution_ids.append(exec_entry.name)
                        except OSError:
                            pass
                        
                        # 计算目录大小
                        dir_size = self._get_dir_size(date_entry.path)
                        
                        # 删除整个日期目录
                        shutil.rmtree(date_entry.path)
                        
                        result.directories_cleaned += 1
                        result.bytes_freed += dir_size
                        result.execution_ids.extend(execution_ids)
                        
                        # 记录清理日志（Requirements: 6.3）
                        logger.info(
                            f"清理过期分布式日志目录: date={date_entry.name}, "
                            f"execution_count={len(execution_ids)}, "
                            f"释放空间={self._format_bytes(dir_size)}"
                        )
                except ValueError:
                    # 不是日期格式的目录，跳过
                    continue
                except OSError as e:
                    logger.warning(f"清理目录失败 {date_entry.path}: {e}")
                    result.errors.append(f"{date_entry.name}: {e}")
        except OSError as e:
            logger.error(f"扫描分布式日志目录失败: {e}")
            result.errors.append(str(e))
        
        return result
    
    async def _cleanup_local_logs(self) -> CleanupResult:
        """
        清理本地任务日志
        
        日志目录结构: data/logs/tasks/{date}/{execution_id}/
        按日期目录名判断是否过期
        """
        result = CleanupResult()
        
        if not self._task_log_dir.exists():
            return result
        
        cutoff_date = datetime.now() - timedelta(days=self._retention_days)
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._sync_cleanup_local_logs,
                cutoff_date,
            )
        except Exception as e:
            logger.error(f"清理本地日志失败: {e}")
            result.errors.append(str(e))
        
        return result
    
    def _sync_cleanup_local_logs(self, cutoff_date: datetime) -> CleanupResult:
        """
        同步清理本地日志（在线程池中执行）
        
        Args:
            cutoff_date: 截止日期
            
        Returns:
            清理结果
        """
        result = CleanupResult()
        
        try:
            for date_entry in os.scandir(self._task_log_dir):
                if not date_entry.is_dir():
                    continue
                
                # 解析日期目录名
                try:
                    dir_date = datetime.strptime(date_entry.name, "%Y-%m-%d")
                    if dir_date < cutoff_date:
                        # 计算目录大小
                        dir_size = self._get_dir_size(date_entry.path)
                        
                        # 删除整个日期目录
                        shutil.rmtree(date_entry.path)
                        
                        result.directories_cleaned += 1
                        result.bytes_freed += dir_size
                        
                        # 记录清理日志
                        logger.info(
                            f"清理过期本地日志目录: date={date_entry.name}, "
                            f"释放空间={self._format_bytes(dir_size)}"
                        )
                except ValueError:
                    # 不是日期格式的目录，跳过（由 _cleanup_dual_channel_logs 处理）
                    continue
                except OSError as e:
                    logger.warning(f"清理目录失败 {date_entry.path}: {e}")
                    result.errors.append(f"{date_entry.name}: {e}")
        except OSError as e:
            logger.error(f"扫描本地日志目录失败: {e}")
            result.errors.append(str(e))
        
        return result
    
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
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                    except OSError:
                        pass
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
    
    async def cleanup_now(self) -> dict:
        """
        立即执行清理（手动触发）
        
        Returns:
            清理结果字典
        """
        start_time = datetime.now()
        
        dual_channel_result = await self._cleanup_dual_channel_logs()
        distributed_result = await self._cleanup_distributed_logs()
        local_result = await self._cleanup_local_logs()
        redis_result = await self._cleanup_redis_streams()
        storage_result = await self._cleanup_log_archives()
        
        duration = (datetime.now() - start_time).total_seconds()
        
        total_dirs = (
            dual_channel_result.directories_cleaned +
            distributed_result.directories_cleaned +
            local_result.directories_cleaned
        )
        total_bytes = (
            dual_channel_result.bytes_freed +
            distributed_result.bytes_freed +
            local_result.bytes_freed
        )
        
        return {
            "dual_channel_cleaned": dual_channel_result.directories_cleaned,
            "distributed_cleaned": distributed_result.directories_cleaned,
            "local_cleaned": local_result.directories_cleaned,
            "redis_streams_checked": redis_result.streams_checked,
            "redis_streams_trimmed": redis_result.streams_trimmed,
            "redis_streams_expired": redis_result.streams_expired,
            "archive_files_deleted": storage_result.files_deleted,
            "archive_bytes_freed": storage_result.bytes_freed,
            "total_directories_cleaned": total_dirs,
            "bytes_freed": total_bytes,
            "bytes_freed_formatted": self._format_bytes(total_bytes),
            "duration_seconds": duration,
            "retention_days": self._retention_days,
            "execution_ids_cleaned": (
                dual_channel_result.execution_ids +
                distributed_result.execution_ids
            ),
            "errors": (
                dual_channel_result.errors +
                distributed_result.errors +
                local_result.errors +
                redis_result.errors +
                storage_result.errors
            ),
        }
    
    async def cleanup_execution(self, execution_id: str) -> dict:
        """
        清理指定执行 ID 的日志
        
        Args:
            execution_id: 执行 ID
            
        Returns:
            清理结果字典
        """
        result = {
            "execution_id": execution_id,
            "cleaned": False,
            "bytes_freed": 0,
            "error": None,
        }
        
        # 1. 清理双通道日志
        dual_channel_path = self._task_log_dir / execution_id
        if dual_channel_path.exists():
            try:
                dir_size = self._get_dir_size(str(dual_channel_path))
                shutil.rmtree(dual_channel_path)
                result["cleaned"] = True
                result["bytes_freed"] += dir_size
                logger.info(
                    f"清理日志目录: execution_id={execution_id}, "
                    f"释放空间={self._format_bytes(dir_size)}"
                )
            except OSError as e:
                result["error"] = str(e)
                logger.error(f"清理日志目录失败 {dual_channel_path}: {e}")
        
        # 2. 清理分布式日志（需要遍历日期目录）
        if self._distributed_log_dir.exists():
            try:
                for date_entry in os.scandir(self._distributed_log_dir):
                    if date_entry.is_dir():
                        exec_path = Path(date_entry.path) / execution_id
                        if exec_path.exists():
                            dir_size = self._get_dir_size(str(exec_path))
                            shutil.rmtree(exec_path)
                            result["cleaned"] = True
                            result["bytes_freed"] += dir_size
                            logger.info(
                                f"清理分布式日志目录: execution_id={execution_id}, "
                                f"释放空间={self._format_bytes(dir_size)}"
                            )
            except OSError as e:
                if result["error"]:
                    result["error"] += f"; {e}"
                else:
                    result["error"] = str(e)
        
        result["bytes_freed_formatted"] = self._format_bytes(result["bytes_freed"])
        return result


# 全局实例
log_cleanup_service = LogCleanupService()
