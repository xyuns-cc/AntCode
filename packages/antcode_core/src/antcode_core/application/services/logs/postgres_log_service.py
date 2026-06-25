"""日志批量持久化服务

为 Master ``LogIngestLoop`` 提供批量 ``append_entries`` API，避免上游
逐条调用 ``task_log_service.write_log`` 导致的 I/O 风暴和异常处理散乱。

**当前实现**：因为 PG 端尚未建表（``task_logs``/``log_entries`` 迁移待后续
P4 / P5 引入），这里实现采用"写盘聚合"策略 —— 把同一 ``run_id`` 的所有
``stdout`` 行合并成一次磁盘写入，由 ``task_log_service`` 落到分日期目录。
后续如果接入 PG/ClickHouse，只需替换 ``_flush_run`` 内部即可，对调用方
完全透明。

设计目标：

- **签名稳定**：``append_entries(entries: Sequence[PostgresLogEntry])``。
- **失败可重试**：函数级失败抛 ``Exception``，上游 ingest loop 不 ACK，
  Redis 消息保留在 pending，靠 ``XAUTOCLAIM`` 重试。
- **零幽灵成功**：失败时不静默吞 — 上游 ``logger.exception`` 是固定调用。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from antcode_core.application.services.logs.task_log_service import task_log_service

_STDERR_TYPES: Final[frozenset[str]] = frozenset({"stderr", "error", "critical"})


@dataclass(frozen=True, slots=True)
class PostgresLogEntry:
    """日志条目 DTO

    字段命名沿用未来 ``task_logs`` 表 schema，避免后续接入 PG 时
    再做一次签名迁移。
    """

    run_id: str
    log_type: str
    content: str
    timestamp: datetime | None = None
    sequence: int = 0
    worker_id: str = ""


class PostgresLogService:
    """批量日志写入服务

    用 ``append_entries`` 一次写一批；同一批内按 ``(run_id, log_type)``
    聚合成 multi-line 内容,一次落盘。
    """

    async def append_entries(self, entries: Sequence[PostgresLogEntry]) -> None:
        """批量写入日志条目。

        - 同 run_id + 同 log_type 的条目会合并为一次 ``write_log`` 调用；
        - 任何 IO 失败都会抛回上游（用于保留消息 pending → 重试）。
        """
        if not entries:
            return

        # 按 (run_id, is_stderr) 分组
        groups: dict[tuple[str, bool], list[PostgresLogEntry]] = {}
        for entry in entries:
            if not entry.run_id:
                continue
            key = (entry.run_id, entry.log_type in _STDERR_TYPES)
            groups.setdefault(key, []).append(entry)

        # 串行写入 — 保留顺序，并避免 task_log_service 内部对同一文件的
        # executor 竞争。后续如果切到 PG，可以改成 ``asyncio.gather``。
        for (run_id, is_stderr), batch in groups.items():
            await self._flush_run(run_id, is_stderr, batch)

    async def _flush_run(
        self,
        run_id: str,
        is_stderr: bool,
        batch: Iterable[PostgresLogEntry],
    ) -> None:
        """把一个 run_id 一批同类型日志拼成单次写入。"""
        paths = task_log_service.generate_log_paths(run_id, run_id)
        target = paths["error_log_path"] if is_stderr else paths["log_file_path"]

        lines: list[str] = []
        for entry in batch:
            ts = entry.timestamp or datetime.now(tz=UTC)
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(
                f"[{ts_str}] [seq={entry.sequence}] [{entry.log_type}] {entry.content}"
            )

        # 一次性 write_log，由 task_log_service 内部走 executor + append
        await task_log_service.write_log(
            log_file_path=target,
            content="\n".join(lines),
            append=True,
            run_id=run_id,
            add_timestamp=False,
        )


postgres_log_service = PostgresLogService()


__all__ = [
    "PostgresLogEntry",
    "PostgresLogService",
    "postgres_log_service",
]
