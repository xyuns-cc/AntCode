"""任务日志管理服务

P2 改造：``write_log`` / ``get_execution_logs`` 改走 ``postgres_log_service``
持久化到 ``task_logs`` 表，不再写磁盘。读取也优先走 PG；本地文件回退
仅用于读 ``log_chunk_receiver`` 早期落盘的兼容路径。
"""
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from antcode_core.application.services.files.async_file_stream_service import file_stream_service
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    postgres_log_service,
    postgres_task_log_service,  # 兼容旧 import 名
)
from antcode_core.common.config import settings

# 兼容旧 ``from .task_log_service import postgres_task_log_service`` 引用
__all__ = ["TaskLogService", "task_log_service", "postgres_task_log_service"]

# 大文件阈值
LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10MB


class TaskLogService:
    """任务日志管理服务"""

    def __init__(self):
        self.log_dir = Path(settings.TASK_LOG_DIR)
        self.max_log_size = settings.TASK_LOG_MAX_SIZE
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def generate_log_paths(self, run_id, task_name):
        """生成日志文件路径"""
        task_log_dir = self.log_dir / datetime.now().strftime("%Y-%m-%d") / run_id
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
        run_id=None,
        add_timestamp=True
    ):
        """写入日志（P2 起改走 PG ``task_logs`` 表）

        ``log_file_path`` 仅用于推断 ``log_type`` (``error.log`` -> ``stderr``，
        其余 -> ``stdout``)。``append`` / ``add_timestamp`` 参数保留签名兼容,
        实际不再产生文件写入。
        """
        if not run_id:
            # 无 run_id 的纯文件写已不再支持 — 静默跳过避免遮蔽真实日志路径。
            logger.debug("write_log 调用缺少 run_id, 跳过 PG 写入: path={}", log_file_path)
            return

        log_type = "stderr" if "error" in (log_file_path or "").lower() else "stdout"
        entry = PostgresLogEntry(
            run_id=run_id,
            log_type=log_type,
            content=str(content),
            timestamp=datetime.now(UTC),
        )
        try:
            await postgres_log_service.append_entries([entry])
        except Exception as e:
            logger.error(f"写入日志到 PG 失败 run_id={run_id}: {e}")


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
                with open(log_file_path, encoding='utf-8') as f:
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
            cutoff_time = datetime.now(UTC).timestamp() - (retention_days * 24 * 3600)
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

    async def get_execution_logs(self, run_id, include_distributed=True):
        """
        获取指定执行 ID 的所有日志。

        P2 起：主路径走 PG ``task_logs``。Redis Stream / Worker 本地文件
        作为 PG 还未刷盘前的回落。

        Args:
            run_id: 执行 ID
            include_distributed: 是否回落到 Worker 上报路径（Redis / 文件）

        Returns:
            ``{"output": str, "error": str}``
        """
        if not run_id:
            return {"output": "", "error": ""}

        # 1. 主路径：PG
        pg_stdout: list[str] = []
        pg_stderr: list[str] = []
        try:
            entries = await postgres_log_service.list_entries(run_id, limit=10000)
            for entry in entries:
                if (entry.log_type or "").lower() == "stderr":
                    pg_stderr.append(entry.content)
                else:
                    pg_stdout.append(entry.content)
        except Exception as e:
            logger.debug(f"PG 日志读取失败 run_id={run_id}: {e}")

        # 2. 回落：Worker 本地落盘 + Redis Stream（用于 PG ingest 还没消费完的场景）
        distributed_output = ""
        distributed_error = ""

        if include_distributed and not (pg_stdout or pg_stderr):
            try:
                from antcode_core.application.services.logs.log_chunk_receiver import log_chunk_receiver

                stdout_path = log_chunk_receiver.get_log_file_path(run_id, "stdout")
                stderr_path = log_chunk_receiver.get_log_file_path(run_id, "stderr")

                if stdout_path.exists():
                    distributed_output = await self.read_log(str(stdout_path))
                if stderr_path.exists():
                    distributed_error = await self.read_log(str(stderr_path))
            except Exception as e:
                logger.debug(f"获取 Worker 本地日志失败: {e}")

            if not distributed_output or not distributed_error:
                redis_output, redis_error = await self._get_redis_stream_logs(run_id)
                if not distributed_output:
                    distributed_output = redis_output
                if not distributed_error:
                    distributed_error = redis_error

        all_output = "\n".join(filter(None, ["\n".join(pg_stdout), distributed_output.strip()]))
        all_error = "\n".join(filter(None, ["\n".join(pg_stderr), distributed_error.strip()]))

        return {
            "output": all_output,
            "error": all_error,
        }

    async def _get_redis_stream_logs(self, run_id: str) -> tuple[str, str]:
        """Redis Stream 回落读取（兼容 per-run + 全局 ingest stream 两种历史路径）。"""
        if not settings.REDIS_URL:
            return "", ""

        try:
            from antcode_core.infrastructure.redis.client import get_redis_client
            from antcode_core.infrastructure.redis.control_plane import redis_namespace
            from antcode_core.infrastructure.redis.keys import RedisKeys
        except Exception as e:
            logger.debug(f"Redis 客户端不可用: {e}")
            return "", ""

        try:
            redis = await get_redis_client()
            keys = RedisKeys(settings.REDIS_NAMESPACE)
            ns = redis_namespace(settings.REDIS_NAMESPACE)
            candidate_keys = [
                keys.log_stream_key(run_id),  # 兼容旧 per-run stream
                f"{ns}:log:ingest",  # 新全局 ingest stream
            ]
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []

            for stream_key in candidate_keys:
                last_id = "0-0"
                while True:
                    result = await redis.xread({stream_key: last_id}, count=200)
                    if not result:
                        break
                    _, messages = result[0]
                    if not messages:
                        break

                    for msg_id, fields in messages:
                        last_id = self._decode_redis_value(msg_id)
                        # ingest stream 是 Proto bytes，per-run 是 JSON / Proto 混合。
                        # 这里走轻量解码 + run_id 过滤。
                        decoded_entries = self._decode_stream_message(fields, run_id)
                        for log_type, content in decoded_entries:
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

    def _decode_stream_message(
        self, fields: dict, run_id_filter: str
    ) -> list[tuple[str, str]]:
        """解码 Stream 消息：返回 ``[(log_type, content), ...]``，按 ``run_id`` 过滤。"""
        # Proto bytes 路径
        proto_raw = fields.get(b"p") or fields.get("p")
        if proto_raw is not None:
            try:
                from antcode_contracts import data_pb2

                if isinstance(proto_raw, str):
                    proto_raw = proto_raw.encode("latin-1")
                batch = data_pb2.LogBatch()
                batch.ParseFromString(proto_raw)
                out: list[tuple[str, str]] = []
                for entry in batch.entries:
                    if run_id_filter and entry.run_id != run_id_filter:
                        continue
                    name = data_pb2.LogType.Name(entry.log_type)
                    log_type = name.removeprefix("LOG_TYPE_").lower() if name.startswith("LOG_TYPE_") else name.lower()
                    out.append((log_type, entry.content or ""))
                return out
            except Exception:
                pass

        # JSON 路径（旧 per-run）
        decoded = self._decode_redis_log(fields)
        msg_run_id = (fields.get(b"run_id") or fields.get("run_id") or "")
        if isinstance(msg_run_id, bytes):
            msg_run_id = msg_run_id.decode("utf-8")
        if run_id_filter and msg_run_id and msg_run_id != run_id_filter:
            return []
        return [(decoded.get("log_type") or "stdout", decoded.get("content") or "")]

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
