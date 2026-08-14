"""Bounded stdout/stderr draining for task processes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from antcode_core.common.logging import sanitize_log_message
from loguru import logger

from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry
from antcode_worker.executor.base import LogSink


@dataclass
class OutputByteBudget:
    max_bytes: int
    consumed_bytes: int = 0
    exceeded: bool = False

    def claim(self, size: int) -> tuple[bool, bool]:
        self.consumed_bytes += size
        allowed = self.max_bytes <= 0 or self.consumed_bytes <= self.max_bytes
        first_exceeded = not allowed and not self.exceeded
        self.exceeded = self.exceeded or not allowed
        return allowed, first_exceeded


@dataclass(frozen=True)
class OutputReadOptions:
    run_id: str
    stream_type: str
    max_lines: int
    log_sink: LogSink
    seq_counter: dict[str, int]
    byte_budget: OutputByteBudget


async def read_output_stream(stream: asyncio.StreamReader, options: OutputReadOptions) -> int:
    """Drain one process pipe while enforcing shared line and byte budgets."""
    count = 0
    while line := await stream.readline():
        count += 1
        allowed_bytes, first_byte_overflow = options.byte_budget.claim(len(line))
        allowed_lines = count <= options.max_lines
        if not allowed_lines or not allowed_bytes:
            _log_overflow(options, count, first_byte_overflow)
            continue
        await _write_log_entry(line, options)
    return count


def _log_overflow(options: OutputReadOptions, count: int, first_byte_overflow: bool) -> None:
    if count == options.max_lines + 1:
        logger.warning(
            "任务 {} {} 输出行数超限 ({}); 后续行继续排空但不持久化",
            options.run_id,
            options.stream_type,
            options.max_lines,
        )
    if first_byte_overflow:
        logger.warning(
            "任务 {} 总输出字节超限 ({}); 后续输出继续排空但不持久化",
            options.run_id,
            options.byte_budget.max_bytes,
        )


async def _write_log_entry(line: bytes, options: OutputReadOptions) -> None:
    content = sanitize_log_message(line.decode("utf-8", errors="replace").rstrip())
    options.seq_counter[options.stream_type] += 1
    await options.log_sink.write(
        LogEntry(
            run_id=options.run_id,
            stream=LogStream.STDOUT if options.stream_type == "stdout" else LogStream.STDERR,
            content=content,
            seq=options.seq_counter[options.stream_type],
            timestamp=datetime.now(),
        )
    )


__all__ = ["OutputByteBudget", "OutputReadOptions", "read_output_stream"]
