"""Data contracts for the offline Crawl Redis fresh-deploy preflight."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_NAMESPACE_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class PreflightRequest:
    namespace: str

    def validate(self) -> None:
        if not _NAMESPACE_PATTERN.fullmatch(self.namespace):
            raise ValueError("Redis namespace 只能包含字母、数字、下划线和连字符")


@dataclass(frozen=True)
class Blocker:
    code: str
    key: str
    detail: str


@dataclass(frozen=True)
class StreamGroupStats:
    name: str
    pending: int
    unconsumed: int | None


@dataclass(frozen=True)
class StreamStats:
    key: str
    entries: int
    groups: tuple[StreamGroupStats, ...]
    envelope_v1: int = 0
    envelope_unsupported: int = 0


@dataclass(frozen=True)
class ExecutionStoreStats:
    key: str
    redis_type: str
    entries: int
    envelope_v1: int
    envelope_unsupported: int


@dataclass(frozen=True)
class PreflightReport:
    namespace: str
    legacy_keys: tuple[str, ...]
    streams: tuple[StreamStats, ...]
    execution_stores: tuple[ExecutionStoreStats, ...]
    blockers: tuple[Blocker, ...]

    @property
    def safe(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["safe"] = self.safe
        return value


class PreflightBlocked(RuntimeError):
    def __init__(self, report: PreflightReport) -> None:
        super().__init__(f"Crawl Redis preflight blocked by {len(report.blockers)} finding(s)")
        self.report = report


__all__ = [
    "Blocker",
    "ExecutionStoreStats",
    "StreamGroupStats",
    "StreamStats",
    "PreflightBlocked",
    "PreflightReport",
    "PreflightRequest",
]
