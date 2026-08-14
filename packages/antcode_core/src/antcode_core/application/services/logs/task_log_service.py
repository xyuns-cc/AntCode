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
from antcode_core.application.services.logs.task_log_readers import (
    LOG_TRUNCATED_MARKER,
    MAX_LOG_READ_BYTES,
    BoundedLogCollector,
    decode_stream_message,
    read_postgres_execution_logs,
    read_postgres_log_text,
    read_redis_execution_logs,
)

# P1-round6 5.3: 单次读日志的字节预算 (统一限制点)。10_000 行 * 1 MiB 单行
# 上限 = 10 GiB Python str, 之前只在 lines 上限, 内存无护栏。默认 32 MiB
# 与 HTTP 响应合理边界对齐; 达到即截断并在结尾追加提示行。
_MAX_LOG_READ_BYTES = MAX_LOG_READ_BYTES
_LOG_TRUNCATED_MARKER = LOG_TRUNCATED_MARKER


def _join_bounded(contents: list[str]) -> str:
    """按 UTF-8 字节预算拼接 contents, 越界立即截断并追加 marker。"""
    collector = BoundedLogCollector()
    for line in contents:
        if not collector.add("stdout", line):
            break
    return collector.texts()[0]


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
            level="ERROR" if normalized_type == "stderr" else "INFO",
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
        try:
            return await read_postgres_log_text(run_id, normalized_type, lines)
        except Exception as exc:
            logger.exception("read_log PG 查询失败 run_id={}", run_id)
            raise RuntimeError(f"PG 日志读取失败: run_id={run_id}") from exc

    async def get_execution_logs(self, run_id: str, include_distributed: bool = True) -> dict[str, str]:
        """获取某次执行的所有日志。

        主路径走 PG；PG 空时回落 Redis ingest stream（覆盖 PG ingest 还没
        刷盘的窗口）。

        Returns:
            ``{"output": str, "error": str}``
        """
        if not run_id:
            return {"output": "", "error": ""}

        pg_stdout = ""
        pg_stderr = ""
        pg_has_entries = False
        pg_failure: Exception | None = None
        try:
            pg_stdout, pg_stderr, pg_has_entries = await read_postgres_execution_logs(run_id)
        except Exception as e:
            logger.exception(f"PG 日志读取失败 run_id={run_id}")
            pg_failure = e

        redis_output = ""
        redis_error = ""
        if include_distributed and not pg_has_entries:
            redis_output, redis_error = await self._get_redis_stream_logs(run_id)
        if pg_failure is not None and not (redis_output or redis_error):
            raise RuntimeError(f"日志存储不可用: run_id={run_id}") from pg_failure

        return {
            "output": "\n".join(filter(None, [pg_stdout, redis_output.strip()])),
            "error": "\n".join(filter(None, [pg_stderr, redis_error.strip()])),
        }

    async def _get_redis_stream_logs(self, run_id: str) -> tuple[str, str]:
        """Redis ingest stream 回落（覆盖 PG 还未 flush 的窗口）。"""
        return await read_redis_execution_logs(run_id)

    def _decode_stream_message(self, fields: dict, run_id_filter: str) -> list[tuple[str, str]]:
        """解码 Stream 消息：返回 ``[(log_type, content), ...]``，按 run_id 过滤。"""
        return decode_stream_message(fields, run_id_filter)


task_log_service = TaskLogService(redis_log_sequence_allocator)
