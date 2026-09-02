"""Master 端爬虫指标聚合与查询服务。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from antcode_core.application.services.workers.worker_resource_probe import persisted_worker_metrics
from antcode_core.domain.models import Worker, WorkerHeartbeat, WorkerStatus
from antcode_core.domain.schemas.worker import SpiderStatsSummary

TOP_DOMAIN_LIMIT = 20
HEALTHY_SUCCESS_RATE = 95.0
WARNING_SUCCESS_RATE = 90.0
LATENCY_ROUNDING_EPSILON = 1e-6


@dataclass
class _ClusterTotals:
    requests: int = 0
    responses: int = 0
    items: int = 0
    errors: int = 0
    latency_weighted: float = 0.0
    rpm: float = 0.0
    status_codes: dict[str, int] = field(default_factory=dict)


@dataclass
class _DomainAggregate:
    domain: str
    requests: int = 0
    weighted_latency: float = 0.0
    weighted_success_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class _HistoryDelta:
    requests: int
    responses: int
    items: int
    errors: int
    latency_total: float
    rpm: float


@dataclass(slots=True)
class _HistoryBucket:
    requests: int = 0
    responses: int = 0
    items: int = 0
    errors: int = 0
    latency_total: float = 0.0
    worker_rpm: dict[int, float] = field(default_factory=dict)

    def add(self, worker_id: int, delta: _HistoryDelta) -> None:
        self.requests += delta.requests
        self.responses += delta.responses
        self.items += delta.items
        self.errors += delta.errors
        self.latency_total += delta.latency_total
        self.worker_rpm[worker_id] = delta.rpm


class SpiderStatsService:
    """提供单 Worker 和集群级别的爬虫统计。"""

    async def get_worker_spider_stats(self, worker_id: int) -> SpiderStatsSummary:
        worker = await Worker.filter(id=worker_id).first()
        if not worker:
            return SpiderStatsSummary()
        spider_stats = self._extract_spider_stats(persisted_worker_metrics(worker))
        return SpiderStatsSummary.from_heartbeat(spider_stats)

    async def get_cluster_spider_stats(self) -> dict[str, Any]:
        workers = await Worker.filter(status=WorkerStatus.ONLINE.value).all()
        totals = _ClusterTotals()
        domains: dict[str, _DomainAggregate] = {}
        for worker in workers:
            spider_stats = self._extract_spider_stats(persisted_worker_metrics(worker))
            if spider_stats:
                self._accumulate_cluster(totals, domains, spider_stats)
        return self._build_cluster_response(totals, domains, len(workers))

    def _accumulate_cluster(
        self,
        totals: _ClusterTotals,
        domains: dict[str, _DomainAggregate],
        spider_stats: dict[str, Any],
    ) -> None:
        responses = spider_stats.get("response_count", 0)
        totals.requests += spider_stats.get("request_count", 0)
        totals.responses += responses
        totals.items += spider_stats.get("item_scraped_count", 0)
        totals.errors += spider_stats.get("error_count", 0)
        totals.latency_weighted += spider_stats.get("avg_latency_ms", 0.0) * responses
        totals.rpm += spider_stats.get("requests_per_minute", 0.0)
        self._accumulate_status_codes(totals.status_codes, spider_stats.get("status_codes", {}))
        self._accumulate_domains(domains, spider_stats.get("domain_stats", []))

    @staticmethod
    def _accumulate_status_codes(aggregate: dict[str, int], status_codes: dict[str, int]) -> None:
        for code, count in status_codes.items():
            aggregate[code] = aggregate.get(code, 0) + count

    @staticmethod
    def _accumulate_domains(
        aggregate: dict[str, _DomainAggregate],
        domain_stats: list[dict[str, Any]],
    ) -> None:
        for stats in domain_stats:
            domain = stats.get("domain", "")
            if not domain:
                continue
            current = aggregate.setdefault(domain, _DomainAggregate(domain=domain))
            requests = stats.get("reqs", 0)
            current.requests += requests
            current.weighted_latency += stats.get("latency", 0.0) * requests
            current.weighted_success_rate += stats.get("successRate", 0.0) * requests

    def _build_cluster_response(
        self,
        totals: _ClusterTotals,
        domains: dict[str, _DomainAggregate],
        worker_count: int,
    ) -> dict[str, Any]:
        avg_latency = totals.latency_weighted / totals.responses if totals.responses else 0.0
        return {
            "totalRequests": totals.requests,
            "totalResponses": totals.responses,
            "totalItemsScraped": totals.items,
            "totalErrors": totals.errors,
            "avgLatencyMs": round(avg_latency, 2),
            "clusterRequestsPerMinute": round(totals.rpm, 2),
            "statusCodes": totals.status_codes,
            "domainStats": self._build_domain_stats(domains),
            "workerCount": worker_count,
        }

    def _build_domain_stats(
        self,
        aggregate: dict[str, _DomainAggregate],
    ) -> list[dict[str, Any]]:
        ranked = sorted(aggregate.values(), key=lambda stats: stats.requests, reverse=True)
        return [self._build_domain_stat(stats) for stats in ranked[:TOP_DOMAIN_LIMIT]]

    @staticmethod
    def _build_domain_stat(stats: _DomainAggregate) -> dict[str, Any]:
        success_rate = stats.weighted_success_rate / stats.requests if stats.requests else 0.0
        latency = stats.weighted_latency / stats.requests if stats.requests else 0.0
        if success_rate >= HEALTHY_SUCCESS_RATE:
            status = "Healthy"
        elif success_rate >= WARNING_SUCCESS_RATE:
            status = "Warning"
        else:
            status = "Critical"
        return {
            "domain": stats.domain,
            "reqs": stats.requests,
            "successRate": round(success_rate, 1),
            "latency": round(latency, 0),
            "status": status,
        }

    async def get_spider_stats_history(self, worker_id: int | None = None, hours: int = 1) -> list[dict]:
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours)
        heartbeats = await self._get_history_heartbeats(worker_id, cutoff_time)
        return self._aggregate_history(heartbeats)

    def _aggregate_history(self, heartbeats: list[WorkerHeartbeat]) -> list[dict[str, Any]]:
        previous_by_worker: dict[int, SpiderStatsSummary] = {}
        buckets: dict[datetime, _HistoryBucket] = {}
        for heartbeat in heartbeats:
            spider_stats = self._extract_spider_stats(heartbeat.metrics)
            if not spider_stats:
                continue
            current = SpiderStatsSummary.from_heartbeat(spider_stats)
            previous = previous_by_worker.get(heartbeat.worker_id)
            previous_by_worker[heartbeat.worker_id] = current
            if previous is None:
                continue
            delta = self._history_delta(heartbeat.worker_id, previous, current)
            minute = self._utc_minute(heartbeat.timestamp)
            buckets.setdefault(minute, _HistoryBucket()).add(heartbeat.worker_id, delta)
        return self._build_history_response(buckets)

    @staticmethod
    async def _get_history_heartbeats(worker_id: int | None, cutoff_time: datetime) -> list[WorkerHeartbeat]:
        if worker_id:
            return (
                await WorkerHeartbeat.filter(worker_id=worker_id, timestamp__gte=cutoff_time)
                .order_by("timestamp")
                .all()
            )
        workers = await Worker.filter(status=WorkerStatus.ONLINE.value).all()
        worker_ids = [worker.id for worker in workers]
        if not worker_ids:
            return []
        return (
            await WorkerHeartbeat.filter(worker_id__in=worker_ids, timestamp__gte=cutoff_time)
            .order_by("timestamp")
            .all()
        )

    def _history_delta(
        self,
        worker_id: int,
        previous: SpiderStatsSummary,
        current: SpiderStatsSummary,
    ) -> _HistoryDelta:
        requests, request_reset = self._counter_delta(current.requestCount, previous.requestCount)
        responses, response_reset = self._counter_delta(current.responseCount, previous.responseCount)
        items, item_reset = self._counter_delta(current.itemScrapedCount, previous.itemScrapedCount)
        errors, error_reset = self._counter_delta(current.errorCount, previous.errorCount)
        resets = [
            name
            for name, reset in (
                ("request_count", request_reset),
                ("response_count", response_reset),
                ("item_scraped_count", item_reset),
                ("error_count", error_reset),
            )
            if reset
        ]
        if resets:
            logger.warning("Worker {} spider counters reset: {}", worker_id, ", ".join(resets))
        return _HistoryDelta(
            requests=requests,
            responses=responses,
            items=items,
            errors=errors,
            latency_total=self._latency_delta(previous, current, response_reset),
            rpm=current.requestsPerMinute,
        )

    @staticmethod
    def _counter_delta(current: int, previous: int) -> tuple[int, bool]:
        if current >= previous:
            return current - previous, False
        return current, True

    @staticmethod
    def _latency_delta(
        previous: SpiderStatsSummary,
        current: SpiderStatsSummary,
        response_reset: bool,
    ) -> float:
        current_total = current.avgLatencyMs * current.responseCount
        if response_reset:
            return current_total
        previous_total = previous.avgLatencyMs * previous.responseCount
        delta = current_total - previous_total
        response_delta = current.responseCount - previous.responseCount
        if delta < -LATENCY_ROUNDING_EPSILON:
            raise ValueError("Spider cumulative latency decreased without a response counter reset")
        if response_delta == 0 and abs(delta) > LATENCY_ROUNDING_EPSILON:
            raise ValueError("Spider cumulative latency changed without new responses")
        return 0.0 if abs(delta) <= LATENCY_ROUNDING_EPSILON else delta

    @staticmethod
    def _utc_minute(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            raise ValueError("Worker heartbeat timestamp must include timezone information")
        return timestamp.astimezone(UTC).replace(second=0, microsecond=0)

    @staticmethod
    def _build_history_response(buckets: dict[datetime, _HistoryBucket]) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": timestamp.isoformat(),
                "requestCount": bucket.requests,
                "responseCount": bucket.responses,
                "itemScrapedCount": bucket.items,
                "errorCount": bucket.errors,
                "avgLatencyMs": round(bucket.latency_total / bucket.responses, 2) if bucket.responses else 0.0,
                "requestsPerMinute": round(sum(bucket.worker_rpm.values()), 2),
            }
            for timestamp, bucket in sorted(buckets.items())
        ]

    @staticmethod
    def _extract_spider_stats(metrics: Mapping[str, Any] | None) -> dict | None:
        """``workers.metrics`` 侧的入参必须先过 ``persisted_worker_metrics``；心跳表那一列
        由 ORM 的 ``JSONField`` 直接给出，与本列不共用契约。"""
        if not metrics:
            return None
        return metrics.get("spider_stats")


spider_stats_service = SpiderStatsService()
