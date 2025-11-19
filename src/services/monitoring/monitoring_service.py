import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import time
import redis.asyncio as redis
from loguru import logger
from tortoise.expressions import Q

from src.core.config import settings
from src.models.monitoring import NodeEvent, NodePerformanceHistory, SpiderMetricsHistory


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
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=False)
        return self._redis

    async def process_stream(self):
        """从 Redis Stream 中读取监控数据并写入数据库。"""
        if not settings.MONITORING_ENABLED:
            return 0

        redis_client = await self._get_redis()
        last_id = await redis_client.get(self.config.stream_last_id_key)
        last_id = last_id.decode() if isinstance(last_id, (bytes, bytearray)) else last_id
        if not last_id:
            last_id = "0-0"

        streams = await redis_client.xread(
            {self.config.stream_key: last_id},
            count=self.config.stream_batch_size,
            block=0,
        )

        if not streams:
            return 0

        processed = 0
        new_last_id = last_id

        for _, messages in streams:
            for message_id, payload in messages:
                try:
                    data = self._parse_stream_payload(payload)
                    if not data:
                        continue
                    await self._persist_data(data)
                    new_last_id = message_id
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error("处理监控数据失败: %s (message_id=%s)", exc, message_id)

        if processed:
            await redis_client.set(self.config.stream_last_id_key, new_last_id)
        return processed

    async def cleanup_old_data(self, days=None):
        """清理过期的监控数据。"""
        keep_days = days if days is not None else self.config.history_keep_days
        cutoff = datetime.utcnow() - timedelta(days=keep_days)
        await NodePerformanceHistory.filter(timestamp__lt=cutoff).delete()
        await SpiderMetricsHistory.filter(timestamp__lt=cutoff).delete()
        await NodeEvent.filter(created_at__lt=cutoff).delete()

    async def get_online_nodes(self):
        """获取当前在线节点及其实时指标。"""
        redis_client = await self._get_redis()
        nodes = await redis_client.smembers(self.config.cluster_set_key)
        result = []

        for node in nodes or []:
            node_id = node.decode()
            status = await redis_client.hgetall(self.config.status_key_tpl.format(node_id=node_id))
            spider = await redis_client.hgetall(self.config.spider_key_tpl.format(node_id=node_id))
            result.append(
                {
                    "node_id": node_id,
                    "status": _decode_hash(status),
                    "spider": _decode_hash(spider),
                }
            )

        return result

    async def get_node_realtime(self, node_id):
        """返回节点最近一小时的实时数据。"""
        redis_client = await self._get_redis()
        key = self.config.history_key_tpl.format(node_id=node_id)
        history = await redis_client.zrange(key, -3600, -1, withscores=True)
        data = []
        for value, score in history or []:
            try:
                payload = json.loads(value)
                payload["timestamp"] = score
                data.append(payload)
            except json.JSONDecodeError:
                continue
        data.sort(key=lambda item: item["timestamp"])
        return data

    async def get_cluster_summary(self):
        """汇总集群级别的实时统计。"""
        nodes = await self.get_online_nodes()
        totals = {
            "nodes_online": len(nodes),
            "requests_total": 0,
            "requests_failed": 0,
            "items_scraped": 0,
            "pages_crawled": 0,
        }

        for node in nodes:
            spider = node.get("spider", {})
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

    async def get_node_history(
        self,
        node_id,
        start_time,
        end_time,
        metric_type="performance",
    ):
        """查询数据库中的历史数据。"""
        if metric_type == "performance":
            queryset = (
                NodePerformanceHistory.filter(
                    Q(node_id=node_id),
                    Q(timestamp__gte=start_time),
                    Q(timestamp__lte=end_time),
                )
                .order_by("timestamp")
                .values()
            )
        else:
            queryset = (
                SpiderMetricsHistory.filter(
                    Q(node_id=node_id),
                    Q(timestamp__gte=start_time),
                    Q(timestamp__lte=end_time),
                )
                .order_by("timestamp")
                .values()
            )

        return [dict(record) for record in await queryset]

    def _parse_stream_payload(self, payload):
        try:
            node_id_raw = payload.get(b"node_id")
            if not node_id_raw:
                return None
            node_id = node_id_raw.decode()

            if b"data" in payload:
                content = json.loads(payload[b"data"].decode())
                content["node_id"] = node_id
                ts_raw = payload.get(b"timestamp")
                if ts_raw:
                    try:
                        content["timestamp"] = float(ts_raw.decode())
                    except Exception:
                        pass
                return content

            if b"event" in payload:
                ts = payload.get(b"timestamp")
                event_time = float(ts.decode()) if ts else time.time()
                return {
                    "node_id": node_id,
                    "timestamp": event_time,
                    "event": payload[b"event"].decode(),
                    "reason": payload.get(b"reason", b"").decode() or None,
                }
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error("解析监控消息失败: %s", exc)
            return None

    async def _persist_data(self, data):
        timestamp = data.get("timestamp")
        if not timestamp:
            return
        try:
            dt = datetime.fromtimestamp(float(timestamp))
        except Exception:
            dt = datetime.utcnow()

        if "event" in data:
            await NodeEvent.create(
                node_id=data.get("node_id"),
                event_type=data.get("event"),
                event_message=data.get("reason"),
                created_at=dt,
            )
            return

        await NodePerformanceHistory.create(
            node_id=data.get("node_id"),
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
            node_id=data.get("node_id"),
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

