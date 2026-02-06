"""监控数据服务模块

提供从 Redis 流归档到数据库并提供查询能力的监控服务。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from loguru import logger
from tortoise.expressions import Q

from antcode_core.common.config import settings
from antcode_core.common.serialization import from_json
from antcode_core.domain.models.monitoring import (
    SpiderMetricsHistory,
    WorkerEvent,
    WorkerPerformanceHistory,
)
from antcode_core.infrastructure.redis import get_redis_client


def _to_int(value, default=0):
    try:
        return int(value)
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

        try:
            redis_client = await self._get_redis()
            last_id = await redis_client.get(self.config.stream_last_id_key)
            last_id = last_id.decode() if isinstance(last_id, (bytes, bytearray)) else last_id
            if not last_id:
                last_id = "0-0"

            streams = await redis_client.xread(
                {self.config.stream_key: last_id},
                count=self.config.stream_batch_size,
                block=5000,
            )

            if not streams:
                return 0
        except asyncio.CancelledError:
            # 应用关闭时会取消任务，这是正常行为
            logger.debug("监控数据流处理被取消（应用正在关闭）")
            return 0
        except Exception as e:
            logger.warning(f"读取监控数据流失败: {e}")
            return 0

        processed = 0
        new_last_id = last_id

        # 批量收集数据
        performance_records = []
        spider_records = []
        event_records = []

        for _, messages in streams:
            for message_id, payload in messages:
                try:
                    data = self._parse_stream_payload(payload)
                    if not data:
                        continue

                    # 收集数据而不是立即插入
                    record = self._prepare_record(data)
                    if record:
                        if record["type"] == "event":
                            event_records.append(record["data"])
                        elif record["type"] == "metrics":
                            performance_records.append(record["performance"])
                            spider_records.append(record["spider"])

                    new_last_id = message_id
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "处理监控数据失败: {} (message_id={})",
                        exc,
                        message_id,
                    )

        # 批量插入数据库
        if event_records:
            await WorkerEvent.bulk_create(event_records)
        if performance_records:
            await WorkerPerformanceHistory.bulk_create(performance_records)
        if spider_records:
            await SpiderMetricsHistory.bulk_create(spider_records)

        if processed:
            await redis_client.set(self.config.stream_last_id_key, new_last_id)
            logger.debug(
                f"批量持久化监控数据: 性能{len(performance_records)}条, "
                f"爬虫{len(spider_records)}条, 事件{len(event_records)}条"
            )

        return processed

    async def cleanup_old_data(self, days=None):
        """清理过期的监控数据（批量操作）"""
        keep_days = days if days is not None else self.config.history_keep_days
        cutoff = datetime.utcnow() - timedelta(days=keep_days)

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
            status = await redis_client.hgetall(
                self.config.status_key_tpl.format(worker_id=worker_id)
            )
            spider = await redis_client.hgetall(
                self.config.spider_key_tpl.format(worker_id=worker_id)
            )
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
            except Exception:
                continue
        data.sort(key=lambda item: item["timestamp"])
        return data

    async def get_cluster_summary(self):
        """汇总集群级别的实时统计。"""
        workers = await self.get_online_workers()
        totals = {
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
                100
                * (totals["requests_total"] - totals["requests_failed"])
                / totals["requests_total"],
                2,
            )
            if totals["requests_total"] > 0
            else 0
        )
        return totals

    async def get_worker_history(
        self,
        worker_id,
        start_time,
        end_time,
        metric_type="performance",
    ):
        """查询数据库中的历史数据。"""
        if metric_type == "performance":
            queryset = (
                WorkerPerformanceHistory.filter(
                    Q(worker_id=worker_id),
                    Q(timestamp__gte=start_time),
                    Q(timestamp__lte=end_time),
                )
                .order_by("timestamp")
                .values()
            )
        else:
            queryset = (
                SpiderMetricsHistory.filter(
                    Q(worker_id=worker_id),
                    Q(timestamp__gte=start_time),
                    Q(timestamp__lte=end_time),
                )
                .order_by("timestamp")
                .values()
            )

        return [dict(record) for record in await queryset]

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
            dt = datetime.fromtimestamp(float(timestamp))
        except Exception:
            dt = datetime.utcnow()

        if "event" in data:
            return {
                "type": "event",
                "data": WorkerEvent(
                    worker_id=data.get("worker_id"),
                    event_type=data.get("event"),
                    event_message=data.get("reason"),
                    created_at=dt,
                ),
            }

        # 性能和爬虫数据一起返回
        return {
            "type": "metrics",
            "performance": WorkerPerformanceHistory(
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
            ),
            "spider": SpiderMetricsHistory(
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
            ),
        }

    async def _persist_data(self, data):
        """单条持久化"""
        timestamp = data.get("timestamp")
        if not timestamp:
            return
        try:
            dt = datetime.fromtimestamp(float(timestamp))
        except Exception:
            dt = datetime.utcnow()

        if "event" in data:
            await WorkerEvent.create(
                worker_id=data.get("worker_id"),
                event_type=data.get("event"),
                event_message=data.get("reason"),
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
