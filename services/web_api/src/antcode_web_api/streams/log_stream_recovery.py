"""Bounded, ordered replay of one PostgreSQL cursor recovery window."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing

from antcode_web_api.streams.ingest_recovery import IngestRecoveryReader, RecoveryWindow
from antcode_web_api.streams.log_stream_guard import StreamGuard
from antcode_web_api.streams.log_stream_replay import (
    ReplayState,
    emit_recovery_frames,
    recovery_frame_size,
)


class BoundedRecoveryReplay:
    def __init__(
        self,
        reader: IngestRecoveryReader,
        guard: StreamGuard,
        *,
        run_id: str,
        window: RecoveryWindow,
        replay_state: ReplayState,
        max_lines: int,
        max_bytes: int,
    ) -> None:
        self._reader = reader
        self._guard = guard
        self._run_id = run_id
        self._window = window
        self._replay_state = replay_state
        self._max_lines = max_lines
        self._max_bytes = max_bytes
        self.sent_lines = 0
        self.sent_bytes = 0
        self.overflowed = False

    async def frames(self) -> AsyncIterator[bytes]:
        entries = self._reader.iter_window(self._window, checkpoint=self._guard.checkpoint)
        async with aclosing(entries):
            async for entry in entries:
                frame_size = recovery_frame_size(self._run_id, entry)
                if self._exceeds_budget(frame_size):
                    self.overflowed = True
                    return
                for frame in emit_recovery_frames(self._run_id, (entry,), self._replay_state):
                    self.sent_lines += 1
                    self.sent_bytes += len(frame)
                    yield frame
                self._replay_state.confirm_persisted(entry)

    def _exceeds_budget(self, frame_size: int) -> bool:
        return self.sent_lines >= self._max_lines or self.sent_bytes + frame_size > self._max_bytes


__all__ = ["BoundedRecoveryReplay"]
