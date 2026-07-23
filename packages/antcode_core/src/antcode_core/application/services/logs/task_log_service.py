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

from antcode_core.application.services.logs.log_sequence_allocator import (
    LogSequenceAllocator,
    redis_log_sequence_allocator,
)
from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    postgres_log_service,
    postgres_task_log_service,  # 兼容旧 import 名
)
from antcode_core.common.config import settings

# P1-round6 5.3: 单次读日志的字节预算 (统一限制点)。10_000 行 * 1 MiB 单行
# 上限 = 10 GiB Python str, 之前只在 lines 上限, 内存无护栏。默认 32 MiB
# 与 HTTP 响应合理边界对齐; 达到即截断并在结尾追加提示行。
_MAX_LOG_READ_BYTES = 32 * 1024 * 1024
_LOG_TRUNCATED_MARKER = "\n… (log truncated to fit response size budget)"


def _join_bounded(contents: list[str]) -> str:
    """按 UTF-8 字节预算拼接 contents, 越界立即截断并追加 marker。"""
    if not contents:
        return ""
    parts: list[str] = []
    total = 0
    for line in contents:
        line_bytes = len(line.encode("utf-8"))
        # 换行符本身也算 1 字节
        if parts and total + line_bytes + 1 > _MAX_LOG_READ_BYTES:
            parts.append(_LOG_TRUNCATED_MARKER)
            break
        if not parts and line_bytes > _MAX_LOG_READ_BYTES:
            # 单条超预算, 硬截段
            parts.append(line.encode("utf-8")[:_MAX_LOG_READ_BYTES].decode("utf-8", "ignore"))
            parts.append(_LOG_TRUNCATED_MARKER)
            break
        parts.append(line)
        total += line_bytes + (1 if len(parts) > 1 else 0)
    return "\n".join(parts)


# 兼容旧 ``from .task_log_service import postgres_task_log_service`` 引用
__all__ = ["TaskLogService", "task_log_service", "postgres_task_log_service"]


class TaskLogService:
    """任务日志管理服务（PG-only）。"""

    def __init__(self, sequence_allocator: LogSequenceAllocator) -> None:
        self._sequence_allocator = sequence_allocator

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

        sequences = await self._sequence_allocator.allocate(run_id, normalized_type, 1)
        if len(sequences) != 1 or sequences[0] <= 0:
            raise RuntimeError(f"日志序号分配结果非法: run_id={run_id} sequences={sequences!r}")
        entry = PostgresLogEntry(
            run_id=run_id,
            log_type=normalized_type,
            content=str(content),
            timestamp=datetime.now(UTC),
            sequence=sequences[0],
        )
        try:
            written = await postgres_log_service.append_entries([entry])
        except Exception:
            logger.exception("写入日志到 PG 失败 run_id={}", run_id)
            raise
        if written != 1:
            raise RuntimeError(f"写入日志到 PG 未持久化唯一行: run_id={run_id} written={written}")

    async def read_log(self, run_id: str, log_type: str, lines: int | None = None) -> str:
        """按 ``run_id`` + ``log_type`` 读取历史日志正文（PG-only）。

        ``lines`` > 0 时只返回末尾 N 行；None/0 返回全部。
        """
        if not run_id:
            return ""

        normalized_type = (log_type or "stdout").lower()
        limit = int(lines) if lines and lines > 0 else 10000
        try:
            entries = await postgres_log_service.list_entries(run_id, limit=limit, log_type=normalized_type)
        except Exception as exc:
            logger.exception("read_log PG 查询失败 run_id={}", run_id)
            raise RuntimeError(f"PG 日志读取失败: run_id={run_id}") from exc
        return _join_bounded([entry.content for entry in entries])

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
        pg_failure: Exception | None = None
        try:
            entries = await postgres_log_service.list_entries(run_id, limit=10000)
            for entry in entries:
                if (entry.log_type or "").lower() == "stderr":
                    pg_stderr.append(entry.content)
                else:
                    pg_stdout.append(entry.content)
        except Exception as e:
            logger.exception(f"PG 日志读取失败 run_id={run_id}")
            pg_failure = e

        redis_output = ""
        redis_error = ""
        if include_distributed and not (pg_stdout or pg_stderr):
            redis_output, redis_error = await self._get_redis_stream_logs(run_id)
        if pg_failure is not None and not (redis_output or redis_error):
            raise RuntimeError(f"日志存储不可用: run_id={run_id}") from pg_failure

        # P1-round6 5.3: 走字节预算拼接, 避免 10000 行 * 1 MiB 单行拷贝到 GB 级
        pg_stdout_text = _join_bounded(pg_stdout)
        pg_stderr_text = _join_bounded(pg_stderr)
        return {
            "output": "\n".join(filter(None, [pg_stdout_text, redis_output.strip()])),
            "error": "\n".join(filter(None, [pg_stderr_text, redis_error.strip()])),
        }

    async def _get_redis_stream_logs(self, run_id: str) -> tuple[str, str]:
        """Redis ingest stream 回落（覆盖 PG 还未 flush 的窗口）。"""
        if not settings.REDIS_URL:
            return "", ""

        redis, candidate_keys = await self._redis_log_sources(run_id)
        lines = await self._read_redis_log_sources(redis, candidate_keys, run_id)
        return "\n".join(lines["stdout"]), "\n".join(lines["stderr"])

    @staticmethod
    async def _redis_log_sources(run_id):
        from antcode_core.infrastructure.redis.client import get_redis_client
        from antcode_core.infrastructure.redis.control_plane import redis_namespace
        from antcode_core.infrastructure.redis.keys import RedisKeys

        redis = await get_redis_client()
        keys = RedisKeys(settings.REDIS_NAMESPACE)
        namespace = redis_namespace(settings.REDIS_NAMESPACE)
        return redis, [keys.log_stream_key(run_id), f"{namespace}:log:ingest"]

    async def _read_redis_log_sources(self, redis, stream_keys, run_id):
        lines: dict[str, list[str]] = {"stdout": [], "stderr": []}
        for stream_key in stream_keys:
            await self._read_redis_log_stream(redis, stream_key, run_id, lines)
        return lines

    async def _read_redis_log_stream(self, redis, stream_key, run_id, lines):
        last_id = "0-0"
        while True:
            result = await redis.xread({stream_key: last_id}, count=200)
            if not result or not result[0][1]:
                return
            for msg_id, fields in result[0][1]:
                last_id = self._decode_redis_value(msg_id)
                self._append_stream_message(lines, fields, run_id)

    def _append_stream_message(self, lines, fields, run_id):
        for log_type, content in self._decode_stream_message(fields, run_id):
            if not content:
                continue
            target = "stderr" if log_type == "stderr" else "stdout"
            lines[target].append(content)

    def _decode_stream_message(self, fields: dict, run_id_filter: str) -> list[tuple[str, str]]:
        """解码 Stream 消息：返回 ``[(log_type, content), ...]``，按 run_id 过滤。"""
        proto_present = b"p" in fields or "p" in fields
        if proto_present:
            proto_raw = fields[b"p"] if b"p" in fields else fields["p"]
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
            except Exception as exc:
                raise ValueError(f"日志 Stream protobuf 解码失败: run_id={run_id_filter}") from exc

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


task_log_service = TaskLogService(redis_log_sequence_allocator)
