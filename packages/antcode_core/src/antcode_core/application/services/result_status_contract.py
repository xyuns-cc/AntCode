"""Result status timing contract shared by direct and Gateway consumers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from antcode_core.domain.models.enums import RuntimeStatus

_TERMINAL_STATUSES = frozenset(
    {
        RuntimeStatus.SUCCESS,
        RuntimeStatus.FAILED,
        RuntimeStatus.CANCELLED,
        RuntimeStatus.TIMEOUT,
        RuntimeStatus.SKIPPED,
    }
)


class ResultStatusContractError(ValueError):
    """A result frame contradicts the server-side status contract."""


@dataclass(frozen=True, slots=True)
class ResultTiming:
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None


def validate_result_timing(
    runtime_status: RuntimeStatus,
    *,
    started_at: datetime | str | None,
    finished_at: datetime | str | None,
    duration_ms: float | str | None,
) -> ResultTiming:
    """Parse and validate timing fields before a result reaches persistence."""
    start = _parse_datetime(started_at, "started_at")
    finish = _parse_datetime(finished_at, "finished_at")
    duration = _parse_duration(duration_ms)
    if runtime_status not in _TERMINAL_STATUSES:
        _require_progress_timing(finish, duration)
    elif finish is None:
        raise ResultStatusContractError("终态结果必须携带 finished_at")
    if start is not None and finish is not None and finish < start:
        raise ResultStatusContractError("finished_at 不能早于 started_at")
    return ResultTiming(start, finish, duration)


def _parse_datetime(value: datetime | str | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ResultStatusContractError(f"{field_name} 不是有效 ISO-8601 时间") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_duration(value: float | str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ResultStatusContractError("duration_ms 不是有效数值") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ResultStatusContractError("duration_ms 必须是有限非负数")
    return parsed


def _require_progress_timing(finished_at: datetime | None, duration_ms: float | None) -> None:
    if finished_at is not None:
        raise ResultStatusContractError("非终态结果不得携带 finished_at")
    if duration_ms not in (None, 0.0):
        raise ResultStatusContractError("非终态结果不得携带 duration_ms")


__all__ = [
    "ResultStatusContractError",
    "ResultTiming",
    "validate_result_timing",
]
