from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

import psutil
import redis.asyncio as redis

from src.tasks.spider.spider.monitoring.config import MonitoringConfig


@dataclass
class SpiderStats:
    tasks_total: int = 0
    tasks_success: int = 0
    tasks_failed: int = 0
    tasks_running: int = 0
    pages_crawled: int = 0
    items_extracted: int = 0
    requests_per_min: float = 0.0
    avg_response_time_ms: int = 0
    error_timeout: int = 0
    error_network: int = 0
    error_parse: int = 0
    error_other: int = 0

    def to_mapping(self) -> Dict[str, Any]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass
class SystemMetrics:
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: int = 0
    disk_percent: float = 0.0
    network_sent_mb: float = 0.0
    network_recv_mb: float = 0.0

    def to_mapping(self) -> Dict[str, Any]:
        return {key: str(value) for key, value in asdict(self).items()}


class MonitoringAgent:
    """运行在爬虫节点上的监控上报代理。"""

    def __init__(self, config: Optional[MonitoringConfig] = None):
        self.config = config or MonitoringConfig.load()
        self.pool = redis.ConnectionPool.from_url(
            self.config.redis_url,
            max_connections=10,
            socket_timeout=10,
            socket_connect_timeout=10,
            socket_keepalive=True
        )
        self.redis = redis.Redis(connection_pool=self.pool)
        self.node_id = self.config.node_id
        self.report_interval = self.config.report_interval
        self._last_network_counters = None
        self.spider_stats = SpiderStats()

    async def collect_system_metrics(self) -> SystemMetrics:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        net_io = psutil.net_io_counters()
        if self._last_network_counters:
            sent_mb = (net_io.bytes_sent - self._last_network_counters.bytes_sent) / 1024 / 1024
            recv_mb = (net_io.bytes_recv - self._last_network_counters.bytes_recv) / 1024 / 1024
        else:
            sent_mb = recv_mb = 0.0
        self._last_network_counters = net_io

        return SystemMetrics(
            cpu_percent=round(cpu_percent, 2),
            memory_percent=round(memory.percent, 2),
            memory_used_mb=memory.used // 1024 // 1024,
            disk_percent=round(disk.percent, 2),
            network_sent_mb=round(sent_mb, 2),
            network_recv_mb=round(recv_mb, 2),
        )

    async def update_spider_stats(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self.spider_stats, key):
                setattr(self.spider_stats, key, value)

    async def increment_error(self, error_type: str):
        attr_name = f"error_{error_type}"
        if hasattr(self.spider_stats, attr_name):
            current = getattr(self.spider_stats, attr_name)
            setattr(self.spider_stats, attr_name, current + 1)

    async def report_metrics(self):
        timestamp = time.time()
        system_metrics = await self.collect_system_metrics()

        status_key = f"monitor:node:{self.node_id}:status"
        spider_key = f"monitor:node:{self.node_id}:spider"
        history_key = f"monitor:node:{self.node_id}:history"
        cluster_key = "monitor:cluster:nodes"

        await self.redis.hset(status_key, mapping={"update_time": int(timestamp), **system_metrics.to_mapping()})
        await self.redis.expire(status_key, 300)

        await self.redis.hset(spider_key, mapping=self.spider_stats.to_mapping())
        await self.redis.expire(spider_key, 300)

        await self.redis.sadd(cluster_key, self.node_id)
        await self.redis.expire(cluster_key, 300)

        history_data = {**system_metrics.to_mapping(), **self.spider_stats.to_mapping()}
        await self.redis.zadd(history_key, {json.dumps(history_data): timestamp})
        cutoff = timestamp - 3600
        await self.redis.zremrangebyscore(history_key, 0, cutoff)

        await self.redis.xadd(
            "monitor:stream:metrics",
            {
                "node_id": self.node_id,
                "timestamp": str(timestamp),
                "data": json.dumps(history_data),
            },
            maxlen=10000,
        )

    async def run_forever(self):
        while True:
            try:
                await self.report_metrics()
            except Exception as exc:  # noqa: BLE001
                print(f"[MonitoringAgent] report failed: {exc}")
            await asyncio.sleep(max(5, self.report_interval))

