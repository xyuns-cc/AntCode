import json
import logging
import os
import socket
import time
import psutil
import redis
from scrapy import signals
from scrapy.exceptions import NotConfigured
from twisted.internet import task

logger = logging.getLogger(__name__)


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class MonitoringConfig:
    def __init__(
        self,
        node_id,
        redis_url,
        status_ttl=300,
        history_ttl=3600,
        cluster_ttl=300,
        stream_maxlen=10000,
        status_key_tpl="monitor:node:{node_id}:status",
        spider_key_tpl="monitor:node:{node_id}:spider",
        history_key_tpl="monitor:node:{node_id}:history",
        cluster_set_key="monitor:cluster:nodes",
        stream_key="monitor:stream:metrics",
    ):
        self.node_id = node_id
        self.redis_url = redis_url
        self.status_ttl = status_ttl
        self.history_ttl = history_ttl
        self.cluster_ttl = cluster_ttl
        self.stream_maxlen = stream_maxlen
        self.status_key_tpl = status_key_tpl
        self.spider_key_tpl = spider_key_tpl
        self.history_key_tpl = history_key_tpl
        self.cluster_set_key = cluster_set_key
        self.stream_key = stream_key


class MonitoringAgent:
    """负责采集并上报节点监控数据。"""

    def __init__(self, config):
        self.config = config
        self.redis = redis.from_url(config.redis_url, decode_responses=False)
        self._last_net = psutil.net_io_counters()
        # 预热 CPU 统计，避免首次返回 0
        psutil.cpu_percent(interval=None)

    def _status_key(self):
        return self.config.status_key_tpl.format(node_id=self.config.node_id)

    def _spider_key(self):
        return self.config.spider_key_tpl.format(node_id=self.config.node_id)

    def _history_key(self):
        return self.config.history_key_tpl.format(node_id=self.config.node_id)

    def mark_online(self):
        timestamp = int(time.time())
        try:
            pipe = self.redis.pipeline()
            pipe.hset(
                self._status_key(),
                mapping={
                    "status": "online",
                    "update_time": timestamp,
                },
            )
            pipe.expire(self._status_key(), self.config.status_ttl)
            pipe.sadd(self.config.cluster_set_key, self.config.node_id)
            pipe.expire(self.config.cluster_set_key, self.config.cluster_ttl)
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("标记节点上线失败：%s", exc)

    def mark_offline(self, reason: str = "shutdown"):
        timestamp = int(time.time())
        try:
            pipe = self.redis.pipeline()
            pipe.hset(
                self._status_key(),
                mapping={
                    "status": f"offline:{reason}",
                    "update_time": timestamp,
                },
            )
            pipe.expire(self._status_key(), self.config.status_ttl)
            pipe.srem(self.config.cluster_set_key, self.config.node_id)
            pipe.xadd(
                self.config.stream_key,
                {
                    "node_id": self.config.node_id,
                    "timestamp": timestamp,
                    "event": "offline",
                    "reason": reason,
                },
                maxlen=self.config.stream_maxlen,
            )
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("标记节点下线失败：%s", exc)

    def collect_system_metrics(self):
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net_io = psutil.net_io_counters()
        if self._last_net:
            sent_mb = (net_io.bytes_sent - self._last_net.bytes_sent) / 1024 / 1024
            recv_mb = (net_io.bytes_recv - self._last_net.bytes_recv) / 1024 / 1024
        else:
            sent_mb = recv_mb = 0
        self._last_net = net_io

        boot_time = _safe_int(psutil.boot_time(), 0)
        return {
            "cpu_percent": round(cpu_percent, 2),
            "memory_percent": round(memory.percent, 2),
            "memory_used_mb": memory.used // 1024 // 1024,
            "disk_percent": round(disk.percent, 2),
            "network_sent_mb": round(sent_mb, 2),
            "network_recv_mb": round(recv_mb, 2),
            "uptime_seconds": int(time.time() - boot_time) if boot_time else 0,
        }

    def collect_spider_metrics(self, stats):
        stats = stats or {}
        response_count = _safe_int(stats.get("downloader/response_count", 0), 0)
        response_time_sum = _safe_float(stats.get("downloader/response_time_sum", 0.0), 0.0)
        avg_response_time = (
            int((response_time_sum / response_count) * 1000) if response_count else 0
        )

        metrics = {
            "items_scraped": _safe_int(stats.get("item_scraped_count", 0), 0),
            "requests_total": _safe_int(stats.get("downloader/request_count", 0), 0),
            "responses_total": response_count,
            "requests_failed": _safe_int(stats.get("downloader/exception_count", 0), 0),
            "pages_crawled": response_count,
            "avg_response_time_ms": avg_response_time,
            "scheduler_enqueued": _safe_int(stats.get("scheduler/enqueued", 0), 0),
            "scheduler_dequeued": _safe_int(stats.get("scheduler/dequeued", 0), 0),
        }

        # 错误统计（如果有的话）
        for key in (
            "error_timeout",
            "error_network",
            "error_parse",
            "error_other",
        ):
            metrics[key] = _safe_int(stats.get(f"errors/{key}", 0), 0)

        return metrics

    def report(self, stats):
        timestamp = int(time.time())
        try:
            system_metrics = self.collect_system_metrics()
            spider_metrics = self.collect_spider_metrics(stats)
            payload = {
                "node_id": self.config.node_id,
                "timestamp": timestamp,
                **system_metrics,
                **spider_metrics,
            }

            pipe = self.redis.pipeline()
            pipe.hset(
                self._status_key(),
                mapping={"update_time": timestamp, **system_metrics, "status": "online"},
            )
            pipe.expire(self._status_key(), self.config.status_ttl)
            pipe.hset(
                self._spider_key(),
                mapping={k: str(v) for k, v in spider_metrics.items()},
            )
            pipe.expire(self._spider_key(), self.config.status_ttl)

            pipe.sadd(self.config.cluster_set_key, self.config.node_id)
            pipe.expire(self.config.cluster_set_key, self.config.cluster_ttl)

            pipe.zadd(self._history_key(), {json.dumps(payload): timestamp})
            pipe.zremrangebyscore(self._history_key(), 0, timestamp - self.config.history_ttl)
            pipe.expire(self._history_key(), self.config.history_ttl)

            pipe.xadd(
                self.config.stream_key,
                {
                    "node_id": self.config.node_id,
                    "timestamp": timestamp,
                    "data": json.dumps(payload),
                },
                maxlen=self.config.stream_maxlen,
            )
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("上报监控数据失败：%s", exc)


class MonitoringExtension:
    """Scrapy 扩展：定期采集并上报节点监控数据。"""

    def __init__(self, crawler, agent, interval):
        self.crawler = crawler
        self.agent = agent
        self.interval = interval
        self._loop = None

    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        if not settings.getbool("MONITORING_ENABLED", True):
            raise NotConfigured

        redis_url = settings.get("REDIS_URL")
        if not redis_url:
            raise NotConfigured("监控需要配置 REDIS_URL")

        node_id = settings.get("SCRAPY_NODE_NAME") or os.getenv("SCRAPY_NODE_NAME")
        if not node_id:
            node_id = socket.gethostname()

        config = MonitoringConfig(
            node_id=node_id,
            redis_url=redis_url,
            status_ttl=settings.getint("MONITOR_STATUS_TTL", 300),
            history_ttl=settings.getint("MONITOR_HISTORY_TTL", 3600),
            cluster_ttl=settings.getint("MONITOR_CLUSTER_TTL", 300),
            stream_maxlen=settings.getint("MONITOR_STREAM_MAXLEN", 10000),
            status_key_tpl=settings.get("MONITOR_STATUS_KEY_TPL", "monitor:node:{node_id}:status"),
            spider_key_tpl=settings.get("MONITOR_SPIDER_KEY_TPL", "monitor:node:{node_id}:spider"),
            history_key_tpl=settings.get("MONITOR_HISTORY_KEY_TPL", "monitor:node:{node_id}:history"),
            cluster_set_key=settings.get("MONITOR_CLUSTER_SET_KEY", "monitor:cluster:nodes"),
            stream_key=settings.get("MONITOR_STREAM_KEY", "monitor:stream:metrics"),
        )

        agent = MonitoringAgent(config)
        interval = settings.getint("MONITOR_REPORT_INTERVAL", 60)

        extension = cls(crawler, agent, interval)
        crawler.signals.connect(extension.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(extension.spider_closed, signal=signals.spider_closed)
        return extension

    def spider_opened(self, spider):
        logger.info("监控扩展启动，节点：%s", self.agent.config.node_id)
        self.agent.mark_online()
        self._loop = task.LoopingCall(self._report_once)
        self._loop.start(self.interval, now=True)

    def spider_closed(self, spider, reason):
        logger.info("监控扩展停止，节点：%s，原因：%s", self.agent.config.node_id, reason)
        if self._loop and getattr(self._loop, "running", False):
            self._loop.stop()
        self.agent.mark_offline(reason)

    def _report_once(self):
        stats = self.crawler.stats.get_stats()
        self.agent.report(stats)

