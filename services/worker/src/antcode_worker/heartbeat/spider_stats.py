"""Validate and aggregate per-run spider statistics for Worker heartbeats."""

from __future__ import annotations

import json
import math
import os
import stat
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

RPM_WINDOW_SECONDS = 60.0
STATS_FILE_MODE = 0o600
DEFAULT_MAX_STATS_FILE_BYTES = 1024 * 1024
MAX_PROTO_INT64 = (1 << 63) - 1
HTTP_STATUS_MIN = 100
HTTP_STATUS_MAX = 599
MAX_DOMAIN_LENGTH = 253


def _nonnegative_int(value: Any, field_name: str) -> int:
    parsed = int(value or 0)
    if parsed < 0 or parsed > MAX_PROTO_INT64:
        raise ValueError(f"{field_name} must fit a non-negative protobuf int64")
    return parsed


def _nonnegative_float(value: Any, field_name: str) -> float:
    parsed = float(value or 0.0)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return parsed


def _add_proto_int(current: int, increment: int, field_name: str) -> int:
    result = current + increment
    if result > MAX_PROTO_INT64:
        raise ValueError(f"cumulative {field_name} exceeds protobuf int64")
    return result


def _add_finite_float(current: float, increment: float, field_name: str) -> float:
    result = current + increment
    if not math.isfinite(result):
        raise ValueError(f"cumulative {field_name} must be finite")
    return result


def _status_codes(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("status_codes must be an object")
    result = {}
    for code, count in value.items():
        parsed_code = int(code)
        if parsed_code < HTTP_STATUS_MIN or parsed_code > HTTP_STATUS_MAX:
            raise ValueError("status code must be between 100 and 599")
        result[str(parsed_code)] = _nonnegative_int(count, f"status_codes[{code}]")
    return result


@dataclass(frozen=True, slots=True)
class DomainStats:
    domain: str
    requests: int
    successes: int
    avg_latency_ms: float

    @classmethod
    def from_mapping(cls, value: Any) -> DomainStats:
        if not isinstance(value, dict):
            raise ValueError("domain_stats entries must be objects")
        domain = str(value.get("domain", "")).strip()
        if not domain or len(domain) > MAX_DOMAIN_LENGTH:
            raise ValueError("domain_stats domain must be between 1 and 253 characters")
        requests = _nonnegative_int(value.get("requests"), "domain requests")
        successes = _nonnegative_int(value.get("successes"), "domain successes")
        if successes > requests:
            raise ValueError("domain successes cannot exceed requests")
        latency = _nonnegative_float(value.get("avg_latency_ms"), "domain avg_latency_ms")
        return cls(domain, requests, successes, latency)


@dataclass(frozen=True, slots=True)
class SpiderRunStats:
    request_count: int
    response_count: int
    item_scraped_count: int
    error_count: int
    avg_latency_ms: float
    status_codes: dict[str, int]
    domain_stats: tuple[DomainStats, ...]

    @classmethod
    def from_mapping(cls, value: Any) -> SpiderRunStats:
        if not isinstance(value, dict):
            raise ValueError("spider stats must be an object")
        domains = value.get("domain_stats", [])
        if not isinstance(domains, list):
            raise ValueError("domain_stats must be an array")
        return cls(
            request_count=_nonnegative_int(value.get("request_count"), "request_count"),
            response_count=_nonnegative_int(value.get("response_count"), "response_count"),
            item_scraped_count=_nonnegative_int(value.get("item_scraped_count"), "item_scraped_count"),
            error_count=_nonnegative_int(value.get("error_count"), "error_count"),
            avg_latency_ms=_nonnegative_float(value.get("avg_latency_ms"), "avg_latency_ms"),
            status_codes=_status_codes(value.get("status_codes", {})),
            domain_stats=tuple(DomainStats.from_mapping(item) for item in domains),
        )


@dataclass(frozen=True, slots=True)
class _DomainAggregate:
    requests: int = 0
    successes: int = 0
    latency_weighted: float = 0.0


@dataclass(frozen=True, slots=True)
class _AccumulatorState:
    request_count: int
    response_count: int
    item_scraped_count: int
    error_count: int
    latency_weighted: float
    status_codes: dict[str, int]
    domains: dict[str, _DomainAggregate]


@dataclass(slots=True)
class SpiderStatsAccumulator:
    """Aggregate validated per-run snapshots for this Worker process."""

    max_file_bytes: int | None = DEFAULT_MAX_STATS_FILE_BYTES
    request_count: int = 0
    response_count: int = 0
    item_scraped_count: int = 0
    error_count: int = 0
    latency_weighted: float = 0.0
    status_codes: dict[str, int] = field(default_factory=dict)
    domains: dict[str, _DomainAggregate] = field(default_factory=dict)
    rate_samples: deque[tuple[float, int]] = field(default_factory=deque)

    def record_file(self, path: str) -> None:
        payload = _read_stats_file(path, self.max_file_bytes)
        stats = SpiderRunStats.from_mapping(payload)
        state = self._next_state(stats)
        os.unlink(path)
        self._commit(state, stats.request_count)

    def record(self, stats: SpiderRunStats) -> None:
        self._commit(self._next_state(stats), stats.request_count)

    def _next_state(self, stats: SpiderRunStats) -> _AccumulatorState:
        return _AccumulatorState(
            request_count=_add_proto_int(self.request_count, stats.request_count, "request_count"),
            response_count=_add_proto_int(self.response_count, stats.response_count, "response_count"),
            item_scraped_count=_add_proto_int(
                self.item_scraped_count,
                stats.item_scraped_count,
                "item_scraped_count",
            ),
            error_count=_add_proto_int(self.error_count, stats.error_count, "error_count"),
            latency_weighted=_add_finite_float(
                self.latency_weighted,
                stats.avg_latency_ms * stats.response_count,
                "latency_weighted",
            ),
            status_codes=self._merged_status_codes(stats.status_codes),
            domains=self._merged_domains(stats.domain_stats),
        )

    def _merged_status_codes(self, updates: dict[str, int]) -> dict[str, int]:
        merged = dict(self.status_codes)
        for code, count in updates.items():
            merged[code] = _add_proto_int(merged.get(code, 0), count, f"status_codes[{code}]")
        return merged

    def _merged_domains(self, updates: tuple[DomainStats, ...]) -> dict[str, _DomainAggregate]:
        merged = dict(self.domains)
        for domain in updates:
            current = merged.get(domain.domain, _DomainAggregate())
            merged[domain.domain] = _DomainAggregate(
                requests=_add_proto_int(current.requests, domain.requests, "domain requests"),
                successes=_add_proto_int(current.successes, domain.successes, "domain successes"),
                latency_weighted=_add_finite_float(
                    current.latency_weighted,
                    domain.avg_latency_ms * domain.requests,
                    "domain latency_weighted",
                ),
            )
        return merged

    def _commit(self, state: _AccumulatorState, request_increment: int) -> None:
        self.request_count = state.request_count
        self.response_count = state.response_count
        self.item_scraped_count = state.item_scraped_count
        self.error_count = state.error_count
        self.latency_weighted = state.latency_weighted
        self.status_codes = state.status_codes
        self.domains = state.domains
        self.rate_samples.append((time.monotonic(), request_increment))

    def snapshot(self) -> dict[str, Any] | None:
        if not self.request_count and not self.response_count and not self.item_scraped_count and not self.error_count:
            return None
        latency = self.latency_weighted / self.response_count if self.response_count else 0.0
        return {
            "request_count": self.request_count,
            "response_count": self.response_count,
            "item_scraped_count": self.item_scraped_count,
            "error_count": self.error_count,
            "avg_latency_ms": round(latency, 2),
            "requests_per_minute": float(self._requests_per_minute()),
            "status_codes": dict(self.status_codes),
            "domain_stats": self._domain_snapshots(),
        }

    def _requests_per_minute(self) -> int:
        cutoff = time.monotonic() - RPM_WINDOW_SECONDS
        while self.rate_samples and self.rate_samples[0][0] < cutoff:
            self.rate_samples.popleft()
        return sum(requests for _, requests in self.rate_samples)

    def _domain_snapshots(self) -> list[dict[str, Any]]:
        result = []
        for domain, stats in self.domains.items():
            success_rate = stats.successes * 100.0 / stats.requests if stats.requests else 0.0
            latency = stats.latency_weighted / stats.requests if stats.requests else 0.0
            result.append(
                {
                    "domain": domain,
                    "reqs": stats.requests,
                    "successRate": round(success_rate, 2),
                    "latency": round(latency, 2),
                }
            )
        return result


def _read_stats_file(path: str, max_bytes: int | None) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise PermissionError("spider stats must be a single regular file")
        if max_bytes is not None and file_stat.st_size > max_bytes:
            raise ValueError(f"spider stats file exceeds {max_bytes} bytes")
        if os.name != "nt" and stat.S_IMODE(file_stat.st_mode) != STATS_FILE_MODE:
            raise PermissionError("spider stats permissions must be 0600")
        if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
            raise PermissionError("spider stats owner must match the Worker")
        with os.fdopen(fd, encoding="utf-8") as stream:
            fd = -1
            return json.load(stream)
    finally:
        if fd >= 0:
            os.close(fd)


class SpiderStatsCollectorMixin:
    _spider_stats_accumulator: SpiderStatsAccumulator | None = None

    def _get_spider_stats_accumulator(self) -> SpiderStatsAccumulator:
        if self._spider_stats_accumulator is None:
            self._spider_stats_accumulator = SpiderStatsAccumulator()
        return self._spider_stats_accumulator

    def record_spider_stats_file(self, path: str) -> None:
        self._get_spider_stats_accumulator().record_file(path)

    def get_spider_stats(self) -> dict[str, Any] | None:
        return self._get_spider_stats_accumulator().snapshot()


__all__ = [
    "DomainStats",
    "SpiderRunStats",
    "SpiderStatsAccumulator",
    "SpiderStatsCollectorMixin",
]
