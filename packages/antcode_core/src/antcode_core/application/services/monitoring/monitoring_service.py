"""提供从 Redis 流归档到数据库并提供查询能力的监控服务。"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast, overload

from loguru import logger
from tortoise.expressions import Q

from antcode_core.common.config import settings
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.common.serialization import from_json
from antcode_core.domain.models.monitoring import (
    SpiderMetricsHistory,
    WorkerEvent,
    WorkerPerformanceHistory,
)
from antcode_core.infrastructure.redis import get_redis_client

from .history_query import WorkerHistoryPageQuery


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@overload
def _to_int(value: object, default: None) -> int | None: ...


@overload
def _to_int(value: object, default: int = 0) -> int: ...


def _to_int(value: object, default: int | None = 0) -> int | None:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decode_hash(raw):
    return {key.decode(): value.decode() for key, value in raw.items()}


class MonitoringSettings:
    def __init__(self):
        self.stream_key = settings.MONITOR_STREAM_KEY
        self.stream_last_id_key = settings.MONITOR_STREAM_LAST_ID_KEY
        self.stream_batch_size = settings.MONITOR_STREAM_BATCH_SIZE
        self.history_keep_days = settings.MONITOR_HISTORY_KEEP_DAYS
        self.status_key_tpl = settings.MONITOR_STATUS_KEY_TPL
        self.spider_key_tpl = settings.MONITOR_SPIDER_KEY_TPL
        self.history_key_tpl = settings.MONITOR_HISTORY_KEY_TPL
        self.cluster_set_key = settings.MONITOR_CLUSTER_SET_KEY
        self.status_ttl = settings.MONITOR_STATUS_TTL


class MonitoringService:
    """监控数据服务：从 Redis 流归档到数据库并提供查询能力。"""

    def __init__(self, config=None):
        self.config = config or MonitoringSettings()

    async def _get_redis(self):
        """获取Redis客户端"""
        return await get_redis_client()

    async def process_stream(self):
        """从 Redis Stream 中读取监控数据并批量写入数据库（优化版本）"""
        if not settings.MONITORING_ENABLED:
            return 0

        stream_batch = await self._read_stream_batch()
        if stream_batch is None:
            return 0
        redis_client, last_id, streams = stream_batch
        records, processed, new_last_id = self._collect_stream_records(streams, last_id)
        await self._persist_stream_records(records)
        if processed:
            await redis_client.set(self.config.stream_last_id_key, new_last_id)
            logger.debug(
                f"批量持久化监控数据: 性能{len(records['performance'])}条, "
                f"爬虫{len(records['spider'])}条, 事件{len(records['event'])}条"
            )
        return processed

    async def _read_stream_batch(self):
        try:
            redis_client = await self._get_redis()
            last_id = await redis_client.get(self.config.stream_last_id_key)
            if isinstance(last_id, (bytes, bytearray)):
                last_id = last_id.decode()
            last_id = last_id or "0-0"
            streams = await redis_client.xread(
                {self.config.stream_key: last_id},
                count=self.config.stream_batch_size,
                block=5000,
            )
            return (redis_client, last_id, streams) if streams else None
        except asyncio.CancelledError:
            logger.debug("监控数据流处理被取消（应用正在关闭）")
            return None

    def _collect_stream_records(self, streams, last_id):
        records: dict[str, list[dict[str, Any]]] = {"performance": [], "spider": [], "event": []}
        processed = 0
        new_last_id = last_id
        for _, messages in streams:
            for message_id, payload in messages:
                if self._collect_stream_record(records, message_id, payload):
                    new_last_id = message_id
                    processed += 1
        return records, processed, new_last_id

    def _collect_stream_record(self, records, message_id, payload):
        try:
            data = self._parse_stream_payload(payload)
            if not data:
                return False
            record = self._prepare_record(data)
            self._append_prepared_record(records, record)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("处理监控数据失败: {} (message_id={})", exc, message_id)
            return False

    @staticmethod
    def _append_prepared_record(records, record):
        if not record:
            return
        if record["type"] == "event":
            records["event"].append(record["data"])
            return
        if record["type"] == "metrics":
            records["performance"].append(record["performance"])
            records["spider"].append(record["spider"])

    @staticmethod
    async def _persist_stream_records(records):
        if records["event"]:
            await WorkerEvent.bulk_create(records["event"])
        if records["performance"]:
            await WorkerPerformanceHistory.bulk_create(records["performance"])
        if records["spider"]:
            await SpiderMetricsHistory.bulk_create(records["spider"])

    async def cleanup_old_data(self, days=None):
        """清理过期的监控数据（批量操作）"""
        keep_days = days if days is not None else self.config.history_keep_days
        cutoff = _utcnow_naive() - timedelta(days=keep_days)

        # 批量删除，并记录删除数量
        perf_deleted = await WorkerPerformanceHistory.filter(timestamp__lt=cutoff).delete()
        spider_deleted = await SpiderMetricsHistory.filter(timestamp__lt=cutoff).delete()
        event_deleted = await WorkerEvent.filter(created_at__lt=cutoff).delete()

        total_deleted = perf_deleted + spider_deleted + event_deleted
        if total_deleted > 0:
            logger.info(
                f"已清理监控数据: 性能{perf_deleted}条, 爬虫{spider_deleted}条, "
                f"事件{event_deleted}条, 共{total_deleted}条 (>= {keep_days}天前)"
            )

    async def get_online_workers(self):
        """获取当前在线 Worker 及其实时指标。"""
        redis_client = await self._get_redis()
        workers = await redis_client.smembers(self.config.cluster_set_key)
        result = []

        for worker in workers or []:
            worker_id = worker.decode()
            status = await redis_client.hgetall(self.config.status_key_tpl.format(worker_id=worker_id))
            spider = await redis_client.hgetall(self.config.spider_key_tpl.format(worker_id=worker_id))
            result.append(
                {
                    "worker_id": worker_id,
                    "status": _decode_hash(status),
                    "spider": _decode_hash(spider),
                }
            )

        return result

    async def get_worker_realtime(self, worker_id):
        """返回 Worker 最近一小时的实时数据。"""
        redis_client = await self._get_redis()
        key = self.config.history_key_tpl.format(worker_id=worker_id)
        history = await redis_client.zrange(key, -3600, -1, withscores=True)
        data = []
        for value, score in history or []:
            try:
                payload = from_json(value)
                payload["timestamp"] = score
                data.append(payload)
            except Exception as exc:
                raise ValueError(f"Worker 实时监控帧损坏: worker_id={worker_id}") from exc
        data.sort(key=lambda item: item["timestamp"])
        return data

    async def get_cluster_summary(self):
        """汇总集群级别的实时统计。"""
        workers = await self.get_online_workers()
        totals: dict[str, int | float] = {
            "workers_online": len(workers),
            "requests_total": 0,
            "requests_failed": 0,
            "items_scraped": 0,
            "pages_crawled": 0,
        }

        for worker in workers:
            spider = worker.get("spider", {})
            totals["requests_total"] += _to_int(spider.get("requests_total"))
            totals["requests_failed"] += _to_int(spider.get("requests_failed"))
            totals["items_scraped"] += _to_int(spider.get("items_scraped"))
            totals["pages_crawled"] += _to_int(spider.get("pages_crawled"))

        totals["success_rate"] = (
            round(
                100 * (totals["requests_total"] - totals["requests_failed"]) / totals["requests_total"],
                2,
            )
            if totals["requests_total"] > 0
            else 0
        )
        return totals

    async def get_worker_history(
        self,
        worker_id: str,
        query: WorkerHistoryPageQuery,
    ) -> tuple[list[dict[str, Any]], int]:
        """查询一页数据库历史数据，并返回筛选范围内总数。"""
        model = WorkerPerformanceHistory if query.metric_type == "performance" else SpiderMetricsHistory
        queryset = model.filter(
            Q(worker_id=worker_id),
            Q(timestamp__gte=query.start_time),
            Q(timestamp__lte=query.end_time),
        )
        total = await queryset.count()
        records = await queryset.order_by("-timestamp").offset((query.page - 1) * query.size).limit(query.size).values()
        records.reverse()
        return [dict(record) for record in records], total

    def _parse_stream_payload(self, payload):
        try:
            worker_id_raw = payload.get(b"worker_id")
            if not worker_id_raw:
                return None
            worker_id = worker_id_raw.decode()

            if b"data" in payload:
                content = from_json(payload[b"data"].decode())
                content["worker_id"] = worker_id
                ts_raw = payload.get(b"timestamp")
                if ts_raw:
                    with contextlib.suppress(Exception):
                        content["timestamp"] = float(ts_raw.decode())
                return content

            if b"event" in payload:
                ts = payload.get(b"timestamp")
                event_time = float(ts.decode()) if ts else time.time()
                return {
                    "worker_id": worker_id,
                    "timestamp": event_time,
                    "event": payload[b"event"].decode(),
                    "reason": payload.get(b"reason", b"").decode() or None,
                }
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error("解析监控消息失败: {}", exc)
            return None

    def _prepare_record(self, data):
        """准备批量插入的记录（不执行数据库操作）"""
        timestamp = data.get("timestamp")
        if not timestamp:
            return None

        try:
            dt = datetime.fromtimestamp(float(timestamp), UTC).replace(tzinfo=None)
        except Exception:
            dt = _utcnow_naive()

        if "event" in data:
            return {
                "type": "event",
                "data": self._worker_event_record(data, dt),
            }
        return {
            "type": "metrics",
            "performance": self._performance_record(data, dt),
            "spider": self._spider_record(data, dt),
        }

    @staticmethod
    def _worker_event_record(data, timestamp):
        return WorkerEvent(
            worker_id=data.get("worker_id"),
            event_type=data.get("event"),
            event_message=normalize_persisted_error_message(data.get("reason")),
            created_at=timestamp,
        )

    @staticmethod
    def _performance_record(data, timestamp):
        return WorkerPerformanceHistory(
            worker_id=data.get("worker_id"),
            timestamp=timestamp,
            cpu_percent=_to_decimal(data.get("cpu_percent")),
            memory_percent=_to_decimal(data.get("memory_percent")),
            memory_used_mb=_to_int(data.get("memory_used_mb"), None),
            disk_percent=_to_decimal(data.get("disk_percent")),
            network_sent_mb=_to_decimal(data.get("network_sent_mb")),
            network_recv_mb=_to_decimal(data.get("network_recv_mb")),
            uptime_seconds=_to_int(data.get("uptime_seconds"), None),
            status=data.get("status", "online"),
        )

    @staticmethod
    def _spider_record(data, timestamp):
        fields = (
            "tasks_total",
            "tasks_success",
            "tasks_failed",
            "tasks_running",
            "pages_crawled",
            "items_scraped",
            "requests_total",
            "requests_failed",
            "avg_response_time_ms",
            "error_timeout",
            "error_network",
            "error_parse",
            "error_other",
        )
        values = {field: _to_int(data.get(field)) for field in fields}
        return SpiderMetricsHistory(worker_id=data.get("worker_id"), timestamp=timestamp, **values)

    async def _persist_data(self, data):
        """单条持久化"""
        timestamp = data.get("timestamp")
        if not timestamp:
            return
        try:
            dt = datetime.fromtimestamp(float(timestamp), UTC).replace(tzinfo=None)
        except Exception:
            dt = _utcnow_naive()

        if "event" in data:
            await WorkerEvent.create(
                worker_id=data.get("worker_id"),
                event_type=data.get("event"),
                event_message=normalize_persisted_error_message(data.get("reason")),
                created_at=dt,
            )
            return

        await WorkerPerformanceHistory.create(
            worker_id=data.get("worker_id"),
            timestamp=dt,
            cpu_percent=_to_decimal(data.get("cpu_percent")),
            memory_percent=_to_decimal(data.get("memory_percent")),
            memory_used_mb=_to_int(data.get("memory_used_mb"), None),
            disk_percent=_to_decimal(data.get("disk_percent")),
            network_sent_mb=_to_decimal(data.get("network_sent_mb")),
            network_recv_mb=_to_decimal(data.get("network_recv_mb")),
            uptime_seconds=_to_int(data.get("uptime_seconds"), None),
            status=data.get("status", "online"),
        )

        await SpiderMetricsHistory.create(
            worker_id=data.get("worker_id"),
            timestamp=dt,
            tasks_total=_to_int(data.get("tasks_total")),
            tasks_success=_to_int(data.get("tasks_success")),
            tasks_failed=_to_int(data.get("tasks_failed")),
            tasks_running=_to_int(data.get("tasks_running")),
            pages_crawled=_to_int(data.get("pages_crawled")),
            items_scraped=_to_int(data.get("items_scraped")),
            requests_total=_to_int(data.get("requests_total")),
            requests_failed=_to_int(data.get("requests_failed")),
            avg_response_time_ms=_to_int(data.get("avg_response_time_ms")),
            error_timeout=_to_int(data.get("error_timeout")),
            error_network=_to_int(data.get("error_network")),
            error_parse=_to_int(data.get("error_parse")),
            error_other=_to_int(data.get("error_other")),
        )


monitoring_service = MonitoringService()
