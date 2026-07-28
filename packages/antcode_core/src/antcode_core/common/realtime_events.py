"""Pure message builders for cross-process realtime events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build_log_line_message(
    run_id: str,
    *,
    log_type: str,
    content: str,
    timestamp: str | None,
    sequence: int,
    source: str,
    event_id: str,
    storage_id: int = 0,
) -> dict[str, Any]:
    """Build a committed ``log_line`` envelope with a stable identity."""
    now = datetime.now(UTC).isoformat()
    return {
        "type": "log_line",
        "run_id": run_id,
        "data": {
            "run_id": run_id,
            "log_type": log_type or "stdout",
            "content": content or "",
            "timestamp": timestamp or now,
            "level": "ERROR" if log_type == "stderr" else "INFO",
            "source": source,
            "sequence": sequence,
            "event_id": event_id,
            "storage_id": storage_id,
        },
        "timestamp": now,
    }


def build_run_status_message(
    run_id: str,
    *,
    status: str,
    progress: float | None,
    message: str,
) -> dict[str, Any]:
    """Build the stable ``run_status`` envelope consumed by SSE clients."""
    return {
        "type": "run_status",
        "run_id": run_id,
        "data": {
            "status": status,
            "progress": progress,
            "message": message,
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


__all__ = ["build_log_line_message", "build_run_status_message"]
