"""任务日志管理服务"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path


from loguru import logger

from antcode_core.common.config import settings
from antcode_core.application.services.files.async_file_stream_service import file_stream_service

# 大文件阈值
LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10MB


class TaskLogService:
    """任务日志管理服务"""

    def __init__(self):
        self.log_dir = Path(settings.TASK_LOG_DIR)
        self.max_log_size = settings.TASK_LOG_MAX_SIZE
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def generate_log_paths(self, execution_id, task_name):
        """生成日志文件路径"""
        task_log_dir = self.log_dir / datetime.now().strftime("%Y-%m-%d") / execution_id
        task_log_dir.mkdir(parents=True, exist_ok=True)

        return {
            "log_file_path": str(task_log_dir / "output.log"),
            "error_log_path": str(task_log_dir / "error.log"),
            "log_dir": str(task_log_dir)
        }
    
    async def write_log(
        self,
        log_file_path,
        content,
        append=True,
        execution_id=None,
        add_timestamp=True
    ):
        """写入日志"""
        try:
            if os.path.exists(log_file_path) and os.path.getsize(log_file_path) > self.max_log_size:
                return

            def write_sync():
                with open(log_file_path, "a" if append else "w", encoding='utf-8') as f:
                    if add_timestamp:
                        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                        f.write(f"[{ts}] {content}\n")
                    else:
                        f.write(f"{content}\n")

            await asyncio.get_event_loop().run_in_executor(None, write_sync)
        except Exception as e:
            logger.error(f"写入日志失败 {log_file_path}: {e}")


    async def read_log(self, log_file_path, lines=None):
        """读取日志文件"""
        try:
            if not os.path.exists(log_file_path):
                return ""

            file_size = os.path.getsize(log_file_path)

            # 大文件使用流式读取
            if file_size > LARGE_FILE_THRESHOLD:
                if lines:
                    return await file_stream_service.get_file_tail(log_file_path, lines)
                chunks = [chunk async for chunk in file_stream_service.stream_file_content(log_file_path)]
                return ''.join(chunks)

            # 小文件直接读取
            def read_sync():
                with open(log_file_path, 'r', encoding='utf-8') as f:
                    if lines is None:
                        return f.read()
                    all_lines = f.readlines()
                    return ''.join(all_lines[-lines:])

            return await asyncio.get_event_loop().run_in_executor(None, read_sync)
        except Exception as e:
            logger.error(f"读取日志失败 {log_file_path}: {e}")
            return ""
    
    async def stream_log_lines(self, log_file_path, max_lines = None):
        """
        按行流式读取日志文件
        
        Args:
            log_file_path: 日志文件路径
            max_lines: 最大行数
            
        Yields:
            str: 日志行内容
        """
        async for line in file_stream_service.stream_file_lines(log_file_path, max_lines):
            yield line
    
    async def search_in_log(self, log_file_path, pattern, 
                          case_sensitive = False, 
                          max_matches = 1000):
        """
        在日志文件中搜索
        
        Args:
            log_file_path: 日志文件路径
            pattern: 搜索模式
            case_sensitive: 是否区分大小写
            max_matches: 最大匹配数
            
        Returns:
            匹配结果列表
        """
        return await file_stream_service.search_in_file(
            log_file_path, pattern, case_sensitive, max_matches
        )
    
    async def get_log_info(self, log_file_path):
        """
        获取日志文件信息（优化版本）
        
        Args:
            log_file_path: 日志文件路径
            
        Returns:
            日志文件信息
        """
        try:
            # 使用优化的文件统计服务
            return await file_stream_service.get_file_stats(log_file_path)
            
        except Exception as e:
            logger.error(f"获取日志文件信息失败 {log_file_path}: {e}")
            return {
                "exists": False,
                "size": 0,
                "lines": 0,
                "modified_time": None,
                "error": str(e)
            }
    
    async def cleanup_old_logs(self, retention_days = None):
        """
        清理过期的日志文件
        
        Args:
            retention_days: 保留天数，默认使用配置值
        """
        if retention_days is None:
            retention_days = settings.TASK_LOG_RETENTION_DAYS
        
        try:
            cutoff_time = datetime.now(timezone.utc).timestamp() - (retention_days * 24 * 3600)
            deleted_count = 0
            
            def cleanup_sync():
                nonlocal deleted_count
                for root, dirs, files in os.walk(self.log_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        if os.path.getmtime(file_path) < cutoff_time:
                            os.remove(file_path)
                            deleted_count += 1
                
                # 删除空目录
                for root, dirs, files in os.walk(self.log_dir, topdown=False):
                    for dir_name in dirs:
                        dir_path = os.path.join(root, dir_name)
                        try:
                            os.rmdir(dir_path)  # 只删除空目录
                        except OSError:
                            pass  # 目录不为空，跳过
            
            await asyncio.get_event_loop().run_in_executor(None, cleanup_sync)
            logger.info(f"清理了 {deleted_count} 个过期日志文件")
            
        except Exception as e:
            logger.error(f"清理过期日志失败: {e}")
    
    async def get_execution_logs(self, execution_id, include_distributed=True):
        """
        获取指定执行ID的所有日志（整合本地日志和分布式日志）
        
        Args:
            execution_id: 执行ID
            include_distributed: 是否包含分布式日志（Worker 上报的日志）
            
        Returns:
            包含输出日志和错误日志的字典
        """
        # 1. 获取本地日志（任务分发阶段的日志）
        local_output = ""
        local_error = ""
        
        for date_dir in self.log_dir.iterdir():
            if date_dir.is_dir():
                execution_dir = date_dir / execution_id
                if execution_dir.exists():
                    local_output = await self.read_log(str(execution_dir / "output.log"))
                    local_error = await self.read_log(str(execution_dir / "error.log"))
                    break
        
        # 2. 获取 Worker 日志（日志双通道持久化）
        distributed_output = ""
        distributed_error = ""

        if include_distributed:
            try:
                from antcode_core.application.services.logs.log_chunk_receiver import log_chunk_receiver

                stdout_path = log_chunk_receiver.get_log_file_path(execution_id, "stdout")
                stderr_path = log_chunk_receiver.get_log_file_path(execution_id, "stderr")

                if stdout_path.exists():
                    distributed_output = await self.read_log(str(stdout_path))
                if stderr_path.exists():
                    distributed_error = await self.read_log(str(stderr_path))
            except Exception as e:
                logger.debug(f"获取 Worker 日志失败: {e}")

            if not distributed_output or not distributed_error:
                redis_output, redis_error = await self._get_redis_stream_logs(execution_id)
                if not distributed_output:
                    distributed_output = redis_output
                if not distributed_error:
                    distributed_error = redis_error
        
        # 3. 合并日志（本地日志在前，分布式日志在后）
        all_output = "\n".join(filter(None, [local_output.strip(), distributed_output.strip()]))
        all_error = "\n".join(filter(None, [local_error.strip(), distributed_error.strip()]))
        
        return {
            "output": all_output,
            "error": all_error
        }

    async def _get_redis_stream_logs(self, execution_id: str) -> tuple[str, str]:
        if not settings.REDIS_URL:
            return "", ""

        try:
            from antcode_core.infrastructure.redis.client import get_redis_client
            from antcode_core.infrastructure.redis.keys import RedisKeys
        except Exception as e:
            logger.debug(f"Redis 客户端不可用: {e}")
            return "", ""

        try:
            redis = await get_redis_client()
            keys = RedisKeys(settings.REDIS_NAMESPACE)
            stream_key = keys.log_stream_key(execution_id)
            last_id = "0-0"
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []

            while True:
                result = await redis.xread({stream_key: last_id}, count=200)
                if not result:
                    break
                _, messages = result[0]
                if not messages:
                    break

                for msg_id, fields in messages:
                    last_id = self._decode_redis_value(msg_id)
                    log_entry = self._decode_redis_log(fields)
                    log_type = log_entry.get("log_type") or "stdout"
                    content = log_entry.get("content") or ""
                    if not content:
                        continue
                    if log_type == "stderr":
                        stderr_lines.append(content)
                    else:
                        stdout_lines.append(content)

            return "\n".join(stdout_lines), "\n".join(stderr_lines)
        except Exception as e:
            logger.debug(f"读取 Redis 日志流失败: {e}")
            return "", ""

    def _decode_redis_log(self, fields: dict) -> dict[str, str]:
        def get_field(name: str):
            return fields.get(name) or fields.get(name.encode("utf-8"))

        return {
            "log_type": self._decode_redis_value(get_field("log_type")),
            "content": self._decode_redis_value(get_field("content")),
            "timestamp": self._decode_redis_value(get_field("timestamp")),
            "sequence": self._decode_redis_value(get_field("sequence")),
        }

    def _decode_redis_value(self, value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else ""


# 创建全局实例
task_log_service = TaskLogService()
