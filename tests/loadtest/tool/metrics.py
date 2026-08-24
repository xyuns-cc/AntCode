from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Generic, TypeVar

from .config import Stage, Thresholds

T = TypeVar("T")
InFlightWindow = tuple[float, float]
OVERLAP_START = 1
OVERLAP_END = -1


@dataclass(frozen=True)
class OperationSample(Generic[T]):
    latency_ms: float
    status_code: int | None
    value: T | None = None
    error: str | None = None

    @property
    def successful(self) -> bool:
        return self.error is None and self.status_code is not None and 100 <= self.status_code < 400


@dataclass(frozen=True)
class LoadSummary:
    requests: int
    successful: int
    failed: int
    server_errors_5xx: int
    error_rate: float
    qps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    status_codes: tuple[tuple[str, int], ...]
    errors: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class LoadReport(Generic[T]):
    name: str
    stage: Stage
    elapsed_seconds: float
    samples: tuple[OperationSample[T], ...]
    peak_concurrency: int

    @property
    def summary(self) -> LoadSummary:
        return summarize(self.samples, self.elapsed_seconds)

    @property
    def values(self) -> tuple[T, ...]:
        return tuple(sample.value for sample in self.samples if sample.successful and sample.value is not None)


def percentile(values: tuple[float, ...], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def peak_overlap(windows: Sequence[InFlightWindow]) -> int:
    """同时在飞的请求数峰值。

    一个场景到底有没有制造出并发，只有这个量能证伪；状态码分布证明不了——
    触发去重锁按 TTL 生效，串行释放的请求照样一路 409。端点相同时先记结束
    再记开始，首尾相接的两个请求不算重叠。
    """
    events = sorted([(start, OVERLAP_START) for start, _ in windows] + [(end, OVERLAP_END) for _, end in windows])
    peak = 0
    live = 0
    for _, delta in events:
        live += delta
        peak = max(peak, live)
    return peak


def _status_counts(samples: tuple[OperationSample[object], ...]) -> tuple[tuple[str, int], ...]:
    labels = ("network_error" if item.status_code is None else str(item.status_code) for item in samples)
    return tuple(sorted(Counter(labels).items()))


def _error_counts(samples: tuple[OperationSample[object], ...]) -> tuple[tuple[str, int], ...]:
    labels = []
    for sample in samples:
        if sample.error:
            labels.append(sample.error)
        elif sample.status_code is not None and sample.status_code >= 400:
            labels.append(f"http_{sample.status_code}")
    return tuple(sorted(Counter(labels).items()))


def summarize(samples: tuple[OperationSample[T], ...], elapsed_seconds: float) -> LoadSummary:
    latencies = tuple(sample.latency_ms for sample in samples)
    successful = sum(sample.successful for sample in samples)
    total = len(samples)
    server_errors = sum(sample.status_code is not None and sample.status_code >= 500 for sample in samples)
    object_samples = samples  # type: ignore[assignment]
    return LoadSummary(
        requests=total,
        successful=successful,
        failed=total - successful,
        server_errors_5xx=server_errors,
        error_rate=(total - successful) / total if total else 1.0,
        qps=total / elapsed_seconds if elapsed_seconds > 0 else 0.0,
        p50_ms=percentile(latencies, 0.50),
        p95_ms=percentile(latencies, 0.95),
        p99_ms=percentile(latencies, 0.99),
        status_codes=_status_counts(object_samples),
        errors=_error_counts(object_samples),
    )


def assert_report(
    report: LoadReport[object],
    thresholds: Thresholds,
    expected_statuses: frozenset[int],
) -> None:
    summary = report.summary
    observed = {int(code) for code, _ in summary.status_codes if code != "network_error"}
    minimum_qps = report.stage.qps * thresholds.min_qps_ratio
    assert summary.requests == report.stage.request_count, summary
    assert summary.successful > 0, summary
    assert summary.server_errors_5xx == 0, summary
    assert summary.error_rate <= thresholds.max_error_rate, summary
    assert summary.qps >= minimum_qps, summary
    assert summary.p50_ms <= thresholds.max_p50_ms, summary
    assert summary.p95_ms <= thresholds.max_p95_ms, summary
    assert summary.p99_ms <= thresholds.max_p99_ms, summary
    assert observed == expected_statuses, summary


def emit_report(report: LoadReport[object], extra: dict[str, object] | None = None) -> None:
    summary = report.summary
    payload = {
        "scenario": report.name,
        "stage": asdict(report.stage),
        "elapsed_seconds": round(report.elapsed_seconds, 6),
        "peak_concurrency": report.peak_concurrency,
        "summary": asdict(summary),
    }
    if extra:
        payload["extra"] = extra
    line = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    print(f"ANTCODE_LOADTEST_RESULT {line}", flush=True)
