"""Validate server-authored retry decision markers on a source run."""

from typing import Any


def retry_was_decided(execution: Any) -> bool:
    result_data = execution.result_data if isinstance(execution.result_data, dict) else {}
    return _valid_retry_intent(execution, result_data.get("retry_intent")) or _valid_cancellation(
        result_data.get("retry_cancellation")
    )


def _valid_retry_intent(execution: Any, marker: Any) -> bool:
    if not isinstance(marker, dict):
        return False
    return (
        marker.get("source_run_id") == execution.run_id
        and _integer(marker.get("retry_count")) == int(execution.retry_count or 0)
        and isinstance(marker.get("retry_time"), str)
    )


def _valid_cancellation(marker: Any) -> bool:
    if not isinstance(marker, dict):
        return False
    return _integer(marker.get("cancelled_by_user_id")) is not None and isinstance(marker.get("cancelled_at"), str)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["retry_was_decided"]
