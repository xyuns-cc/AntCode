"""Bounded query values for archived Worker monitoring data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WorkerHistoryPageQuery:
    start_time: datetime
    end_time: datetime
    metric_type: str
    page: int
    size: int


__all__ = ["WorkerHistoryPageQuery"]
