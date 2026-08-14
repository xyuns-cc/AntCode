"""Data contracts and key parsing for the offline Crawl Redis upgrade."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class UpgradeMode(StrEnum):
    FRESH_DEPLOY = "fresh-deploy"
    EXISTING_UPGRADE = "existing-upgrade"


class LegacyKeyKind(StrEnum):
    STREAM = "stream"
    DEAD_LETTER = "dead_letter"
    PROGRESS = "progress"
    CHECKPOINT = "checkpoint"
    DEDUP = "dedup"
    WORKERS = "workers"
    CRAWL_RUNTIME = "crawl_runtime"
    UNKNOWN = "unknown"


_SEGMENT = r"[A-Za-z0-9_-]+"
_RULE_PATTERNS = (
    (LegacyKeyKind.STREAM, re.compile(rf"^rule:(?P<project>{_SEGMENT}):stream:(?P<priority>0|5|9)$")),
    (LegacyKeyKind.PROGRESS, re.compile(rf"^rule:(?P<project>{_SEGMENT}):progress:(?P<batch>{_SEGMENT})$")),
    (LegacyKeyKind.CHECKPOINT, re.compile(rf"^rule:(?P<project>{_SEGMENT}):checkpoint:(?P<batch>{_SEGMENT})$")),
    (LegacyKeyKind.DEDUP, re.compile(rf"^rule:(?P<project>{_SEGMENT}):dedup$")),
    (LegacyKeyKind.WORKERS, re.compile(rf"^rule:(?P<project>{_SEGMENT}):workers:(?P<batch>{_SEGMENT})$")),
    (LegacyKeyKind.DEAD_LETTER, re.compile(rf"^rule:(?P<project>{_SEGMENT}):dead_letter$")),
)


@dataclass(frozen=True)
class UpgradeRequest:
    mode: UpgradeMode
    namespace: str
    apply: bool = False
    writers_stopped: bool = False
    paused_batches: frozenset[tuple[str, str]] = frozenset()
    paused_projects: frozenset[str] = frozenset()

    def validate(self) -> None:
        if not re.fullmatch(_SEGMENT, self.namespace):
            raise ValueError("Redis namespace 只能包含字母、数字、下划线和连字符")
        if self.mode == UpgradeMode.FRESH_DEPLOY and self.apply:
            raise ValueError("fresh-deploy 模式不允许 --apply")
        if self.mode == UpgradeMode.EXISTING_UPGRADE and not self.writers_stopped:
            raise ValueError("existing-upgrade 必须显式确认所有 Redis writers 已停止")
        if self.mode == UpgradeMode.FRESH_DEPLOY and (self.paused_batches or self.paused_projects):
            raise ValueError("fresh-deploy 模式不接受 paused project/batch 声明")


@dataclass(frozen=True)
class ParsedLegacyKey:
    key: str
    kind: LegacyKeyKind
    project_id: str | None = None
    batch_id: str | None = None


@dataclass(frozen=True)
class Blocker:
    code: str
    key: str
    detail: str


@dataclass(frozen=True)
class StateKeyStats:
    source: str
    target: str
    kind: str
    redis_type: str
    items: int
    pttl_ms: int
    authorized_paused_state: bool


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
class UpgradeReport:
    mode: str
    namespace: str
    apply_requested: bool
    legacy_keys: tuple[str, ...]
    state_keys: tuple[StateKeyStats, ...]
    streams: tuple[StreamStats, ...]
    execution_stores: tuple[ExecutionStoreStats, ...]
    blockers: tuple[Blocker, ...]
    migrated_sources: tuple[str, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["safe"] = self.safe
        return value


class UpgradeBlocked(RuntimeError):
    def __init__(self, report: UpgradeReport) -> None:
        super().__init__(f"Crawl Redis upgrade blocked by {len(report.blockers)} finding(s)")
        self.report = report


def parse_legacy_key(key: str) -> ParsedLegacyKey:
    if key.startswith("crawl:"):
        return ParsedLegacyKey(key, LegacyKeyKind.CRAWL_RUNTIME)
    for kind, pattern in _RULE_PATTERNS:
        match = pattern.fullmatch(key)
        if match is not None:
            values = match.groupdict()
            return ParsedLegacyKey(key, kind, values.get("project"), values.get("batch"))
    return ParsedLegacyKey(key, LegacyKeyKind.UNKNOWN)


__all__ = [
    "Blocker",
    "ExecutionStoreStats",
    "LegacyKeyKind",
    "ParsedLegacyKey",
    "StateKeyStats",
    "StreamGroupStats",
    "StreamStats",
    "UpgradeBlocked",
    "UpgradeMode",
    "UpgradeReport",
    "UpgradeRequest",
    "parse_legacy_key",
]
