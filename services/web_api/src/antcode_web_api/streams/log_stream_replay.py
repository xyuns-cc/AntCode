"""History and cursor-recovery replay state for SSE log streams."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from antcode_web_api.streams.ingest_event_id import format_postgres_cursor
from antcode_web_api.streams.sse import (
    build_history_complete_message,
    build_log_line_message,
    format_sse_event,
    normalize_sequence,
)


@dataclass
class ReplayState:
    """Stable identities already emitted during this connection."""

    sequences: set[tuple[str, int]] = field(default_factory=set)
    event_ids: set[str] = field(default_factory=set)
    storage_ids: set[int] = field(default_factory=set)
    storage_identities: dict[int, tuple[str | None, tuple[str, int] | None]] = field(default_factory=dict)
    storage_watermark: int = 0

    def record(
        self,
        log_type: str,
        *,
        sequence: int | None,
        event_id: str | None,
        storage_id: int = 0,
    ) -> None:
        if event_id:
            self.event_ids.add(event_id)
        if sequence is not None and sequence >= 0:
            self.sequences.add((log_type, sequence))
        if storage_id > 0:
            self.storage_ids.add(storage_id)
            sequence_key = (log_type, sequence) if sequence is not None and sequence >= 0 else None
            self.storage_identities[storage_id] = (event_id, sequence_key)

    def record_recovery(self, entry: dict[str, Any]) -> None:
        self._record_entry(entry)

    def record_message(self, message: dict[str, Any]) -> None:
        if message.get("type") != "log_line":
            return
        self._record_entry(message.get("data") or {})

    def overlaps_entry(self, entry: dict[str, Any]) -> bool:
        return self._overlaps_identity(entry)

    def confirm_persisted(self, entry: dict[str, Any]) -> None:
        storage_id = _positive_int(entry.get("storage_id"))
        if storage_id is not None:
            self._discard_storage_identity(storage_id)
        event_id = entry.get("event_id")
        if isinstance(event_id, str):
            self.event_ids.discard(event_id)
        sequence = normalize_sequence(entry.get("sequence"))
        if sequence is not None and sequence >= 0:
            self.sequences.discard((str(entry.get("log_type") or "stdout"), sequence))

    def overlaps(self, message: dict[str, Any]) -> bool:
        if message.get("type") != "log_line":
            return False
        return self._overlaps_identity(message.get("data") or {})

    def advance_storage_watermark(self, storage_id: int) -> None:
        if storage_id < 0:
            raise ValueError("PG storage watermark 不得为负数")
        self.storage_watermark = max(self.storage_watermark, storage_id)
        covered = [item_id for item_id in self.storage_identities if item_id <= self.storage_watermark]
        for item_id in covered:
            self._discard_storage_identity(item_id)

    def _discard_storage_identity(self, storage_id: int) -> None:
        event_id, sequence_key = self.storage_identities.pop(storage_id, (None, None))
        self.storage_ids.discard(storage_id)
        if event_id:
            self.event_ids.discard(event_id)
        if sequence_key is not None:
            self.sequences.discard(sequence_key)

    def _record_entry(self, data: dict[str, Any]) -> None:
        event_id = data.get("event_id")
        sequence = normalize_sequence(data.get("sequence"))
        storage_id = _positive_int(data.get("storage_id"))
        self.record(
            str(data.get("log_type") or "stdout"),
            sequence=sequence,
            event_id=event_id if isinstance(event_id, str) else None,
            storage_id=storage_id or 0,
        )

    def _overlaps_identity(self, data: dict[str, Any]) -> bool:
        storage_id = _positive_int(data.get("storage_id"))
        if storage_id is not None and storage_id <= self.storage_watermark:
            return True
        if storage_id is not None and storage_id in self.storage_ids:
            return True
        event_id = data.get("event_id")
        if isinstance(event_id, str) and event_id in self.event_ids:
            return True
        if storage_id is not None:
            return False
        sequence = normalize_sequence(data.get("sequence"))
        if sequence is None or sequence < 0:
            return False
        return (str(data.get("log_type") or "stdout"), sequence) in self.sequences


@dataclass(frozen=True)
class _ReplayFrame:
    payload: bytes
    log_type: str
    sequence: int | None
    event_id: str | None
    storage_id: int


def history_frame_size(run_id: str, entry: dict[str, Any]) -> int:
    """Measure one encoded frame without retaining it across history pages."""
    return len(
        _build_replay_frame(
            run_id,
            entry,
            source=entry.get("source", "pg_history"),
            include_pg_cursor=False,
        ).payload
    )


def recovery_frame_size(run_id: str, entry: dict[str, Any]) -> int:
    return len(_build_replay_frame(run_id, entry, source="pg_recovery", include_pg_cursor=True).payload)


def emit_history_entry(run_id: str, entry: dict[str, Any], state: ReplayState) -> bytes:
    frame = _build_replay_frame(
        run_id,
        entry,
        source=entry.get("source", "pg_history"),
        include_pg_cursor=False,
    )
    state.record(
        frame.log_type,
        sequence=frame.sequence,
        event_id=frame.event_id,
        storage_id=frame.storage_id,
    )
    return frame.payload


def emit_gap_entry(run_id: str, entry: dict[str, Any], state: ReplayState) -> bytes:
    frame = _build_replay_frame(run_id, entry, source="pg_gap", include_pg_cursor=True)
    state.record(
        frame.log_type,
        sequence=frame.sequence,
        event_id=frame.event_id,
        storage_id=frame.storage_id,
    )
    return frame.payload


def emit_recovery_frames(
    run_id: str,
    entries: Iterable[dict[str, Any]],
    state: ReplayState,
) -> Iterator[bytes]:
    for entry in entries:
        if state.overlaps_entry(entry):
            continue
        frame = _build_replay_frame(run_id, entry, source="pg_recovery", include_pg_cursor=True)
        yield frame.payload
        state.record_recovery(entry)


def history_complete_frame(sent: int, *, truncated: bool) -> bytes:
    complete = build_history_complete_message(sent, truncated=truncated)
    return format_sse_event(complete["type"], complete)


def stream_cursor_frame(storage_id: int) -> bytes:
    event_id = postgres_event_id(storage_id)
    message = {"type": "stream_cursor", "storage_id": storage_id}
    return format_sse_event("stream_cursor", message, event_id=event_id)


def format_live_message(message: dict[str, Any]) -> bytes:
    event_name = str(message.get("type") or "log_line")
    return format_sse_event(event_name, message)


def _build_replay_frame(
    run_id: str,
    entry: dict[str, Any],
    *,
    source: str,
    include_pg_cursor: bool,
) -> _ReplayFrame:
    event_id = entry.get("event_id")
    message = build_log_line_message(
        run_id,
        log_type=entry["log_type"],
        content=entry["content"],
        timestamp=entry["timestamp"] or None,
        sequence=entry["sequence"],
        source=source,
        event_id=event_id,
        storage_id=int(entry.get("storage_id") or 0),
    )
    payload = format_sse_event(
        "log_line",
        message,
        event_id=_entry_postgres_event_id(entry) if include_pg_cursor else None,
    )
    return _ReplayFrame(
        payload,
        entry["log_type"],
        entry["sequence"],
        event_id,
        int(entry.get("storage_id") or 0),
    )


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and value.isdecimal():
        normalized = int(value)
    else:
        return None
    return normalized if normalized > 0 else None


def _entry_postgres_event_id(entry: dict[str, Any]) -> str | None:
    storage_id = _positive_int(entry.get("storage_id"))
    return postgres_event_id(storage_id) if storage_id is not None else None


postgres_event_id = format_postgres_cursor


__all__ = [
    "ReplayState",
    "emit_gap_entry",
    "emit_history_entry",
    "emit_recovery_frames",
    "format_live_message",
    "history_frame_size",
    "history_complete_frame",
    "postgres_event_id",
    "recovery_frame_size",
    "stream_cursor_frame",
]
