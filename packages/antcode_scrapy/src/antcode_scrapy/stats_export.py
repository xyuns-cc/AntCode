"""Scrapy extension exporting a validated per-run metrics sidecar."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scrapy import signals

MILLISECONDS_PER_SECOND = 1_000.0
SUCCESS_STATUS_MIN = 200
SUCCESS_STATUS_MAX = 399
ERROR_STATUS_MIN = 400
STATS_PATH_ENV = "ANTCODE_SPIDER_STATS_PATH"


@dataclass(slots=True)
class _DomainStats:
    requests: int = 0
    successes: int = 0
    latency_ms: float = 0.0


class SpiderStatsExporter:
    """Collect response-level latency/domain data and export Scrapy totals."""

    def __init__(self, stats: Any, output_path: str):
        self._stats = stats
        self._output_path = output_path
        self._domains: dict[str, _DomainStats] = defaultdict(_DomainStats)
        self._response_count = 0
        self._latency_ms = 0.0

    @classmethod
    def from_crawler(cls, crawler: Any) -> SpiderStatsExporter:
        output_path = os.environ.get(STATS_PATH_ENV, "").strip()
        if not output_path:
            raise RuntimeError(f"{STATS_PATH_ENV} is required")
        extension = cls(crawler.stats, output_path)
        crawler.signals.connect(extension.response_received, signal=signals.response_received)
        crawler.signals.connect(extension.spider_closed, signal=signals.spider_closed)
        return extension

    def response_received(self, response: Any, request: Any, spider: Any) -> None:
        del spider
        latency_ms = float(request.meta.get("download_latency", 0.0) or 0.0) * MILLISECONDS_PER_SECOND
        status = int(response.status)
        self._response_count += 1
        self._latency_ms += latency_ms
        domain = urlsplit(response.url).hostname or ""
        if not domain:
            return
        aggregate = self._domains[domain]
        aggregate.requests += 1
        aggregate.latency_ms += latency_ms
        if SUCCESS_STATUS_MIN <= status <= SUCCESS_STATUS_MAX:
            aggregate.successes += 1

    def spider_closed(self, spider: Any, reason: str) -> None:
        del spider, reason
        payload = self._build_payload()
        output = Path(self._output_path)
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = output.with_suffix(f"{output.suffix}.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)

    def _build_payload(self) -> dict[str, Any]:
        status_codes = self._status_codes()
        errors = self._error_count(status_codes)
        average_latency = self._latency_ms / self._response_count if self._response_count else 0.0
        return {
            "request_count": int(self._stats.get_value("downloader/request_count", 0) or 0),
            "response_count": int(self._stats.get_value("downloader/response_count", 0) or 0),
            "item_scraped_count": int(self._stats.get_value("item_scraped_count", 0) or 0),
            "error_count": errors,
            "avg_latency_ms": round(average_latency, 2),
            "status_codes": status_codes,
            "domain_stats": self._domain_stats(),
        }

    def _status_codes(self) -> dict[str, int]:
        prefix = "downloader/response_status_count/"
        return {
            key.removeprefix(prefix): int(value)
            for key, value in self._stats.get_stats().items()
            if key.startswith(prefix)
        }

    def _error_count(self, status_codes: dict[str, int]) -> int:
        http_errors = sum(count for code, count in status_codes.items() if int(code) >= ERROR_STATUS_MIN)
        downloader_errors = int(self._stats.get_value("downloader/exception_count", 0) or 0)
        spider_errors = sum(
            int(value) for key, value in self._stats.get_stats().items() if key.startswith("spider_exceptions/")
        )
        return http_errors + downloader_errors + spider_errors

    def _domain_stats(self) -> list[dict[str, Any]]:
        return [
            {
                "domain": domain,
                "requests": stats.requests,
                "successes": stats.successes,
                "avg_latency_ms": round(stats.latency_ms / stats.requests, 2) if stats.requests else 0.0,
            }
            for domain, stats in sorted(self._domains.items())
        ]


__all__ = ["STATS_PATH_ENV", "SpiderStatsExporter"]
