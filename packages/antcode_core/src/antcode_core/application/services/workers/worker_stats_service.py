"""节点统计服务 - 节点指标与历史数据管理

从 worker_service.py 拆分，专注于统计和指标相关功能。
"""

from datetime import UTC, datetime, timedelta

from antcode_core.application.services.workers.worker_resource_probe import persisted_worker_metrics
from antcode_core.domain.models import Worker, WorkerHeartbeat, WorkerStatus
from antcode_core.domain.schemas.worker import WorkerAggregateStats


class WorkerStatsService:
    """节点统计服务"""

    async def get_aggregate_stats(self) -> WorkerAggregateStats:
        """获取节点聚合统计（含爬虫统计）"""
        workers = await Worker.all()

        total_workers = len(workers)
        online_workers = sum(1 for n in workers if n.status == WorkerStatus.ONLINE)
        offline_workers = sum(1 for n in workers if n.status == WorkerStatus.OFFLINE)
        maintenance_workers = sum(1 for n in workers if n.status == WorkerStatus.MAINTENANCE)

        total_projects = 0
        total_tasks = 0
        running_tasks = 0
        total_envs = 0
        total_cpu = 0.0
        total_memory = 0.0
        workers_with_metrics = 0

        # 爬虫统计聚合
        total_requests = 0
        total_responses = 0
        total_items_scraped = 0
        total_errors = 0
        total_latency_weighted = 0.0
        total_rpm = 0.0

        for worker in workers:
            # 这一列的读取契约在 persisted_worker_metrics：坏列降级成"这台没上报过"，
            # 只把它排除在均值分母之外，不让它把整个集群统计打成 500。
            metrics = persisted_worker_metrics(worker)
            if metrics:
                total_projects += metrics.get("projectCount", 0)
                total_tasks += metrics.get("taskCount", 0)
                running_tasks += metrics.get("runningTasks", 0)
                total_envs += metrics.get("envCount", 0)
                total_cpu += metrics.get("cpu", 0)
                total_memory += metrics.get("memory", 0)
                workers_with_metrics += 1

                # 聚合爬虫统计
                spider_stats = metrics.get("spider_stats")
                if spider_stats:
                    req_count = spider_stats.get("request_count", 0)
                    resp_count = spider_stats.get("response_count", 0)
                    total_requests += req_count
                    total_responses += resp_count
                    total_items_scraped += spider_stats.get("item_scraped_count", 0)
                    total_errors += spider_stats.get("error_count", 0)
                    total_latency_weighted += spider_stats.get("avg_latency_ms", 0.0) * resp_count
                    total_rpm += spider_stats.get("requests_per_minute", 0.0)

        avg_cpu = total_cpu / workers_with_metrics if workers_with_metrics > 0 else 0
        avg_memory = total_memory / workers_with_metrics if workers_with_metrics > 0 else 0
        avg_latency = total_latency_weighted / total_responses if total_responses > 0 else 0.0

        return WorkerAggregateStats(
            totalWorkers=total_workers,
            onlineWorkers=online_workers,
            offlineWorkers=offline_workers,
            maintenanceWorkers=maintenance_workers,
            totalProjects=total_projects,
            totalTasks=total_tasks,
            runningTasks=running_tasks,
            totalEnvs=total_envs,
            avgCpu=round(avg_cpu, 1),
            avgMemory=round(avg_memory, 1),
            # 爬虫统计
            totalRequests=total_requests,
            totalResponses=total_responses,
            totalItemsScraped=total_items_scraped,
            totalErrors=total_errors,
            avgLatencyMs=round(avg_latency, 2),
            clusterRequestsPerMinute=round(total_rpm, 2),
        )

    async def get_metrics_history(self, worker_id: int, hours: int = 24) -> list[dict]:
        """
        获取节点的历史指标数据
        返回指定时间范围内的心跳记录
        """
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours)

        heartbeats = (
            await WorkerHeartbeat.filter(worker_id=worker_id, timestamp__gte=cutoff_time).order_by("timestamp").all()
        )

        result = []
        for hb in heartbeats:
            metrics = hb.metrics or {}
            result.append(
                {
                    "timestamp": hb.timestamp.isoformat(),
                    "cpu": metrics.get("cpu", 0),
                    "memory": metrics.get("memory", 0),
                    "disk": metrics.get("disk", 0),
                    "taskCount": metrics.get("taskCount", 0),
                    "runningTasks": metrics.get("runningTasks", 0),
                    "uptime": metrics.get("uptime", 0),
                }
            )

        return result

    async def get_cluster_metrics_history(self, hours: int = 24) -> dict:
        """
        获取集群的历史聚合指标
        按时间点聚合所有节点的指标，返回平均值、最大值、最小值
        """
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours)

        # 获取所有节点
        workers = await Worker.all()
        worker_ids = [n.id for n in workers]

        if not worker_ids:
            return {
                "timestamps": [],
                "cpu": {"avg": [], "max": [], "min": []},
                "memory": {"avg": [], "max": [], "min": []},
            }

        # 获取所有心跳记录
        heartbeats = (
            await WorkerHeartbeat.filter(worker_id__in=worker_ids, timestamp__gte=cutoff_time)
            .order_by("timestamp")
            .all()
        )

        time_format, interval_hours = self._history_interval(hours)
        time_data = self._bucket_heartbeats(heartbeats, time_format, interval_hours)
        timestamps = sorted(time_data)
        return {
            "timestamps": timestamps,
            "cpu": self._metric_summary(time_data, timestamps, "cpu"),
            "memory": self._metric_summary(time_data, timestamps, "memory"),
        }

    @staticmethod
    def _history_interval(hours):
        if hours <= 24:
            return "%Y-%m-%d %H:00", 1
        if hours <= 168:
            return None, 4
        return "%Y-%m-%d", 1

    @staticmethod
    def _bucket_heartbeats(heartbeats, time_format, interval_hours):
        time_data: dict[str, dict[str, list[float]]] = {}
        for heartbeat in heartbeats:
            time_key = WorkerStatsService._heartbeat_bucket(heartbeat, time_format, interval_hours)
            bucket = time_data.setdefault(time_key, {"cpu": [], "memory": [], "disk": []})
            metrics = heartbeat.metrics or {}
            for metric in bucket:
                if metrics.get(metric) is not None:
                    bucket[metric].append(metrics.get(metric, 0))
        return time_data

    @staticmethod
    def _heartbeat_bucket(heartbeat, time_format, interval_hours):
        if time_format:
            return heartbeat.timestamp.strftime(time_format)
        hour_bucket = (heartbeat.timestamp.hour // interval_hours) * interval_hours
        return heartbeat.timestamp.strftime(f"%Y-%m-%d {hour_bucket:02d}:00")

    @staticmethod
    def _metric_summary(time_data, timestamps, metric):
        values = [time_data[timestamp][metric] for timestamp in timestamps]
        return {
            "avg": [round(sum(items) / len(items), 1) if items else 0 for items in values],
            "max": [round(max(items), 1) if items else 0 for items in values],
            "min": [round(min(items), 1) if items else 0 for items in values],
        }

    async def get_spider_metrics_history(self, worker_id: int, hours: int = 24) -> list[dict]:
        """
        获取节点爬虫指标历史

        Args:
            worker_id: 节点内部 ID
            hours: 查询时间范围（小时）

        Returns:
            爬虫指标历史数据列表
        """
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours)

        heartbeats = (
            await WorkerHeartbeat.filter(worker_id=worker_id, timestamp__gte=cutoff_time).order_by("timestamp").all()
        )

        result = []
        for hb in heartbeats:
            metrics = hb.metrics or {}
            spider_stats = metrics.get("spider_stats")

            if spider_stats:
                result.append(
                    {
                        "timestamp": hb.timestamp.isoformat(),
                        "requestCount": spider_stats.get("request_count", 0),
                        "responseCount": spider_stats.get("response_count", 0),
                        "itemScrapedCount": spider_stats.get("item_scraped_count", 0),
                        "errorCount": spider_stats.get("error_count", 0),
                        "avgLatencyMs": spider_stats.get("avg_latency_ms", 0.0),
                        "requestsPerMinute": spider_stats.get("requests_per_minute", 0.0),
                    }
                )

        return result


# 创建服务实例
worker_stats_service = WorkerStatsService()
