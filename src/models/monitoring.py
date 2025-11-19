from tortoise import fields
from tortoise.models import Model


class NodePerformanceHistory(Model):
    """节点系统性能历史记录（按分钟聚合）"""

    id = fields.BigIntField(pk=True)
    node_id = fields.CharField(max_length=100, index=True, description="节点标识")
    timestamp = fields.DatetimeField(index=True, description="采集时间")

    cpu_percent = fields.DecimalField(max_digits=5, decimal_places=2, null=True)
    memory_percent = fields.DecimalField(max_digits=5, decimal_places=2, null=True)
    memory_used_mb = fields.IntField(null=True)
    disk_percent = fields.DecimalField(max_digits=5, decimal_places=2, null=True)
    network_sent_mb = fields.DecimalField(max_digits=10, decimal_places=2, null=True)
    network_recv_mb = fields.DecimalField(max_digits=10, decimal_places=2, null=True)
    uptime_seconds = fields.BigIntField(null=True)

    status = fields.CharField(max_length=20, default="online", description="节点状态")
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "node_performance_history"
        indexes = ("node_id", "timestamp")


class SpiderMetricsHistory(Model):
    """爬虫业务指标历史记录（按分钟聚合）"""

    id = fields.BigIntField(pk=True)
    node_id = fields.CharField(max_length=100, index=True, description="节点标识")
    timestamp = fields.DatetimeField(index=True, description="采集时间")

    tasks_total = fields.IntField(default=0)
    tasks_success = fields.IntField(default=0)
    tasks_failed = fields.IntField(default=0)
    tasks_running = fields.IntField(default=0)

    pages_crawled = fields.IntField(default=0)
    items_scraped = fields.IntField(default=0)
    requests_total = fields.IntField(default=0)
    requests_failed = fields.IntField(default=0)
    avg_response_time_ms = fields.IntField(default=0)

    error_timeout = fields.IntField(default=0)
    error_network = fields.IntField(default=0)
    error_parse = fields.IntField(default=0)
    error_other = fields.IntField(default=0)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "spider_metrics_history"
        indexes = ("node_id", "timestamp")


class NodeEvent(Model):
    """节点事件日志"""

    id = fields.BigIntField(pk=True)
    node_id = fields.CharField(max_length=100, index=True, description="节点标识")
    event_type = fields.CharField(max_length=50, index=True, description="事件类型")
    event_message = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta:
        table = "node_events"
        indexes = (("node_id", "created_at"), ("event_type", "created_at"))

