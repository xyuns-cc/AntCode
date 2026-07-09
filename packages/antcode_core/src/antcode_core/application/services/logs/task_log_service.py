"""任务日志管理服务。

重构态：任务日志统一走 PG ``task_logs`` 表，磁盘文件 / Redis per-run
stream 相关能力（``file_stream_service`` / ``log_chunk_receiver`` /
``generate_log_paths``）已随 ``services/files`` 模块与 migration 39
（``remove_task_run_log_file_paths``）一同下线。

对外只保留两个真实入口：
- ``write_log(run_id, log_type, content)``：写入 ``task_logs``。
- ``get_execution_logs(run_id)``：读取 ``task_logs``，Redis Stream 作为
  PG ingest 尚未刷盘时的短期回落。
"""
from datetime import UTC, datetime

from loguru import logger

from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    postgres_log_service,
    postgres_task_log_service,  # 兼容旧 import 名
)
from antcode_core.common.config import settings

# 兼容旧 ``from .task_log_service import postgres_task_log_service`` 引用
__all__ = ["TaskLogService", "task_log_service", "postgres_task_log_service"]


class TaskLogService:
    """任务日志管理服务（PG-only）。"""

    async def write_log(self, run_id: str, log_type: str, content: str) -> None:
        """把一条日志写入 ``task_logs`` 表。

        Args:
            run_id: 执行 ID
            log_type: ``stdout`` / ``stderr`` / ``system`` 等
            content: 日志正文
        """
        if not run_id:
            logger.debug("write_log 缺少 run_id，跳过")
            return

        normalized_type = (log_type or "stdout").lower()
        if normalized_type not in ("stdout", "stderr", "system"):
            normalized_type = "stdout"

        entry = PostgresLogEntry(
            run_id=run_id,
            log_type=normalized_type,
            content=str(content),
            timestamp=datetime.now(UTC),
        )
        try:
            await postgres_log_service.append_entries([entry])
        except Exception as e:
            logger.error(f"写入日志到 PG 失败 run_id={run_id}: {e}")

    async def read_log(self, run_id: str, log_type: str, lines: int | None = None) -> str:
        """按 ``run_id`` + ``log_type`` 读取历史日志正文（PG-only）。

        ``lines`` > 0 时只返回末尾 N 行；None/0 返回全部。
        """
        if not run_id:
            return ""

        normalized_type = (log_type or "stdout").lower()
        limit = int(lines) if lines and lines > 0 else 10000
        try:
            entries = await postgres_log_service.list_entries(
                run_id, limit=limit, log_type=normalized_type
            )
        except Exception as exc:
            logger.debug("read_log PG 查询失败 run_id={}: {}", run_id, exc)
            return ""
        return "\n".join(entry.content for entry in entries)

    async def get_execution_logs(self, run_id: str, include_distributed: bool = True) -> dict[str, str]:
        """获取某次执行的所有日志。

        主路径走 PG；PG 空时回落 Redis ingest stream（覆盖 PG ingest 还没
        刷盘的窗口）。

        Returns:
            ``{"output": str, "error": str}``
        """
        if not run_id:
            return {"output": "", "error": ""}

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

        redis_output = ""
        redis_error = ""
        if include_distributed and not (pg_stdout or pg_stderr):
            redis_output, redis_error = await self._get_redis_stream_logs(run_id)

        return {
            "output": "\n".join(filter(None, ["\n".join(pg_stdout), redis_output.strip()])),
            "error": "\n".join(filter(None, ["\n".join(pg_stderr), redis_error.strip()])),
        }

    async def _get_redis_stream_logs(self, run_id: str) -> tuple[str, str]:
        """Redis ingest stream 回落（覆盖 PG 还未 flush 的窗口）。"""
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
                        for log_type, content in self._decode_stream_message(fields, run_id):
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

    def _decode_stream_message(self, fields: dict, run_id_filter: str) -> list[tuple[str, str]]:
        """解码 Stream 消息：返回 ``[(log_type, content), ...]``，按 run_id 过滤。"""
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
                    log_type = (
                        name.removeprefix("LOG_TYPE_").lower()
                        if name.startswith("LOG_TYPE_")
                        else name.lower()
                    )
                    out.append((log_type, entry.content or ""))
                return out
            except Exception:
                pass

        decoded = self._decode_redis_log(fields)
        msg_run_id = fields.get(b"run_id") or fields.get("run_id") or ""
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


task_log_service = TaskLogService()
