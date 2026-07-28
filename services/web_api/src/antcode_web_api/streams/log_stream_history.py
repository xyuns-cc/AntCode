"""Bounded initial history replay for one SSE connection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from loguru import logger

from antcode_web_api.streams.ingest_history import IngestLogHistoryReader, PostgresHistoryWindow
from antcode_web_api.streams.log_stream_guard import StreamGuard, StreamGuardViolation
from antcode_web_api.streams.log_stream_replay import (
    ReplayState,
    emit_history_entry,
    history_complete_frame,
    history_frame_size,
    stream_cursor_frame,
)
from antcode_web_api.streams.sse import format_sse_event


class HistoryReplayError(RuntimeError):
    """The initial history snapshot could not be read consistently."""


class BoundedHistoryReplay:
    def __init__(
        self,
        reader: IngestLogHistoryReader,
        guard: StreamGuard,
        *,
        run_id: str,
        replay_state: ReplayState,
        max_lines: int,
        max_bytes: int,
    ) -> None:
        self._reader = reader
        self._guard = guard
        self._run_id = run_id
        self._state = replay_state
        self._max_lines = max_lines
        self._max_bytes = max_bytes

    async def frames(self) -> AsyncIterator[bytes]:
        yield format_sse_event(
            "historical_logs_start",
            {"type": "historical_logs_start", "timestamp": datetime.now(UTC).isoformat()},
        )
        try:
            async for frame in self._replay_snapshot():
                yield frame
        except StreamGuardViolation:
            raise
        except Exception as exc:
            logger.warning("日志流历史读取失败 run_id={}: {}", self._run_id, exc)
            raise HistoryReplayError() from exc

    async def _replay_snapshot(self) -> AsyncIterator[bytes]:
        window = await self._reader.plan_latest(
            self._run_id,
            max_lines=self._max_lines,
            max_bytes=self._max_bytes,
            size_of=lambda entry: history_frame_size(self._run_id, entry),
            checkpoint=self._guard.checkpoint,
        )
        sent = 0
        async for entry in self._reader.iter_window(window, checkpoint=self._guard.checkpoint):
            sent += 1
            yield emit_history_entry(self._run_id, entry, self._state)
        if isinstance(window, PostgresHistoryWindow):
            self._state.advance_storage_watermark(window.snapshot_id)
            yield stream_cursor_frame(window.snapshot_id)
        yield history_complete_frame(sent, truncated=window.truncated)


__all__ = ["BoundedHistoryReplay", "HistoryReplayError"]
