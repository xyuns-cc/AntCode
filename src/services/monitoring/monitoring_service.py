from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import time
from typing import Any, Optional

from loguru import logger
from tortoise.expressions import Q

from src.core.config import settings
from src.models.monitoring import NodeEvent, NodePerformanceHistory, SpiderMetricsHistory
from src.infrastructure.redis import get_redis_client
from src.utils.serialization import from_json


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decode_hash(raw: dict[bytes, bytes]) -> dict[str, str]:
    return {key.decode(): value.decode() for key, value in raw.items()}


class MonitoringSettings:
    def __init__(self) -> None:
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

    def __init__(self, config: Optional[MonitoringSettings] = None) -> None:
        self.config = config or MonitoringSettings()

    async def _get_redis(self) -> Any:
        """获取Redis客户端"""
        return await get_redis_client()

    async def process_stream(self) -> int:
        """从 Redis Stream 中读取监控数据并批量写入数据库（优化版本）"""
        import asyncio

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
                        if record['type'] == 'event':
                            event_records.append(record['data'])
                        elif record['type'] == 'metrics':
                            performance_records.append(record['performance'])
                            spider_records.append(record['spider'])

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
            await NodeEvent.bulk_create(event_records)
        if performance_records:
            await NodePerformanceHistory.bulk_create(performance_records)
        if spider_records:
            await SpiderMetricsHistory.bulk_create(spider_records)

        if processed:
            await redis_client.set(self.config.stream_last_id_key, new_last_id)
            logger.debug(
                f"批量持久化监控数据: 性能{len(performance_records)}条, "
                f"爬虫{len(spider_records)}条, 事件{len(event_records)}条"
            )

        return processed

    async def cleanup_old_data(self, days: Optional[int] = None) -> None:
        """清理过期的监控数据（批量操作）"""
        keep_days = days if days is not None else self.config.history_keep_days
        cutoff = datetime.utcnow() - timedelta(days=keep_days)

        # 批量删除，并记录删除数量
        perf_deleted = await NodePerformanceHistory.filter(timestamp__lt=cutoff).delete()
        spider_deleted = await SpiderMetricsHistory.filter(timestamp__lt=cutoff).delete()
        event_deleted = await NodeEvent.filter(created_at__lt=cutoff).delete()

        total_deleted = perf_deleted + spider_deleted + event_deleted
        if total_deleted > 0:
            logger.info(
                f"已清理监控数据: 性能{perf_deleted}条, 爬虫{spider_deleted}条, "
                f"事件{event_deleted}条, 共{total_deleted}条 (>= {keep_days}天前)"
            )

    async def get_online_nodes(self) -> list[dict[str, Any]]:
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

    async def get_node_realtime(self, node_id: str) -> list[dict[str, Any]]:
        """返回节点最近一小时的实时数据。"""
        redis_client = await self._get_redis()
        key = self.config.history_key_tpl.format(node_id=node_id)
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

    async def get_cluster_summary(self) -> dict[str, Any]:
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

    def _parse_stream_payload(self, payload: dict[bytes, bytes]) -> Optional[dict[str, Any]]:
        try:
            node_id_raw = payload.get(b"node_id")
            if not node_id_raw:
                return None
            node_id = node_id_raw.decode()

            if b"data" in payload:
                content = from_json(payload[b"data"].decode())
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
            logger.error("解析监控消息失败: {}", exc)
            return None

    def _prepare_record(self, data: dict[str, Any]) -> Optional[dict[str, Any]]:
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
                'type': 'event',
                'data': NodeEvent(
                    node_id=data.get("node_id"),
                    event_type=data.get("event"),
                    event_message=data.get("reason"),
                    created_at=dt,
                )
            }

        # 性能和爬虫数据一起返回
        return {
            'type': 'metrics',
            'performance': NodePerformanceHistory(
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
            ),
            'spider': SpiderMetricsHistory(
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
        }

monitoring_service = MonitoringService()
