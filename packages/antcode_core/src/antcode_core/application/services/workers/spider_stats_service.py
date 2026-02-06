"""爬虫统计服务 - Master 端爬虫指标聚合与查询

提供单节点和集群级别的爬虫统计查询功能。
"""

from datetime import UTC, datetime, timedelta

from antcode_core.domain.models import Worker, WorkerHeartbeat, WorkerStatus
from antcode_core.domain.schemas.worker import SpiderStatsSummary


class SpiderStatsService:
    """爬虫统计服务"""

    async def get_worker_spider_stats(self, worker_id: int) -> SpiderStatsSummary:
        """
        获取单 Worker 爬虫统计

        Args:
            worker_id: Worker 内部 ID

        Returns:
            SpiderStatsSummary 对象
        """
        worker = await Worker.filter(id=worker_id).first()
        if not worker:
            return SpiderStatsSummary()

        # 从节点 metrics 中提取爬虫统计
        spider_stats = self._extract_spider_stats(worker.metrics)
        return SpiderStatsSummary.from_heartbeat(spider_stats)

    async def get_cluster_spider_stats(self) -> dict:
        """
        获取集群爬虫统计聚合

        Returns:
            包含聚合统计和各节点统计的字典
        """
        workers = await Worker.filter(status=WorkerStatus.ONLINE.value).all()

        # 聚合统计
        total_requests = 0
        total_responses = 0
        total_items = 0
        total_errors = 0
        total_latency_weighted = 0.0
        total_rpm = 0.0
        status_codes_agg: dict[str, int] = {}
        domain_stats_agg: dict[str, dict] = {}

        worker_stats = []

        for worker in workers:
            spider_stats = self._extract_spider_stats(worker.metrics)
            if not spider_stats:
                continue

            req_count = spider_stats.get("request_count", 0)
            resp_count = spider_stats.get("response_count", 0)
            items = spider_stats.get("item_scraped_count", 0)
            errors = spider_stats.get("error_count", 0)
            latency = spider_stats.get("avg_latency_ms", 0.0)
            rpm = spider_stats.get("requests_per_minute", 0.0)
            codes = spider_stats.get("status_codes", {})
            domains = spider_stats.get("domain_stats", [])

            total_requests += req_count
            total_responses += resp_count
            total_items += items
            total_errors += errors
            total_latency_weighted += latency * resp_count
            total_rpm += rpm

            # 聚合状态码
            for code, count in codes.items():
                status_codes_agg[code] = status_codes_agg.get(code, 0) + count

            # 聚合域名统计
            for ds in domains:
                domain = ds.get("domain", "")
                if not domain:
                    continue
                if domain not in domain_stats_agg:
                    domain_stats_agg[domain] = {
                        "domain": domain,
                        "reqs": 0,
                        "totalLatency": 0.0,
                        "successCount": 0,
                        "totalCount": 0,
                    }
                agg = domain_stats_agg[domain]
                reqs = ds.get("reqs", 0)
                agg["reqs"] += reqs
                agg["totalLatency"] += ds.get("latency", 0) * reqs
                agg["totalCount"] += reqs
                if ds.get("successRate", 0) >= 90:
                    agg["successCount"] += reqs

            worker_stats.append({
                "workerId": worker.public_id,
                "workerName": worker.name,
                "stats": SpiderStatsSummary.from_heartbeat(spider_stats).model_dump(),
            })

        # 计算加权平均延迟
        avg_latency = (
            total_latency_weighted / total_responses if total_responses > 0 else 0.0
        )

        # 转换域名统计为列表格式
        domain_stats_list = []
        for domain, agg in sorted(
            domain_stats_agg.items(), key=lambda x: x[1]["reqs"], reverse=True
        )[:20]:  # Top 20
            success_rate = (
                agg["successCount"] / agg["totalCount"] * 100
                if agg["totalCount"] > 0
                else 0
            )
            latency_avg = (
                agg["totalLatency"] / agg["totalCount"]
                if agg["totalCount"] > 0
                else 0
            )
            domain_stats_list.append({
                "domain": domain,
                "reqs": agg["reqs"],
                "successRate": round(success_rate, 1),
                "latency": round(latency_avg, 0),
                "status": "Healthy" if success_rate >= 95 else ("Warning" if success_rate >= 90 else "Critical"),
            })

        return {
            "cluster": {
                "totalRequests": total_requests,
                "totalResponses": total_responses,
                "totalItemsScraped": total_items,
                "totalErrors": total_errors,
                "avgLatencyMs": round(avg_latency, 2),
                "clusterRequestsPerMinute": round(total_rpm, 2),
                "statusCodes": status_codes_agg,
                "domainStats": domain_stats_list,
            },
            "workers": worker_stats,
            "workerCount": len(worker_stats),
        }

    async def get_spider_stats_history(
        self, worker_id: int | None = None, hours: int = 1
    ) -> list[dict]:
        """
        获取爬虫统计历史趋势

        Args:
            worker_id: Worker 内部 ID（可选，为空则返回集群聚合）
            hours: 查询时间范围（小时）

        Returns:
            历史数据点列表
        """
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours)

        if worker_id:
            # 单节点历史
            heartbeats = (
                await WorkerHeartbeat.filter(
                    worker_id=worker_id, timestamp__gte=cutoff_time
                )
                .order_by("timestamp")
                .all()
            )
        else:
            # 集群历史 - 获取所有在线节点的心跳
            workers = await Worker.filter(status=WorkerStatus.ONLINE.value).all()
            worker_ids = [n.id for n in workers]
            if not worker_ids:
                return []

            heartbeats = (
                await WorkerHeartbeat.filter(
                    worker_id__in=worker_ids, timestamp__gte=cutoff_time
                )
                .order_by("timestamp")
                .all()
            )

        # 按时间聚合
        time_data: dict[str, dict] = {}

        for hb in heartbeats:
            # 按分钟聚合
            time_key = hb.timestamp.strftime("%Y-%m-%d %H:%M")

            if time_key not in time_data:
                time_data[time_key] = {
                    "requests": 0,
                    "responses": 0,
                    "items": 0,
                    "errors": 0,
                    "latency_sum": 0.0,
                    "latency_count": 0,
                    "rpm": 0.0,
                }

            spider_stats = self._extract_spider_stats(hb.metrics)
            if spider_stats:
                data = time_data[time_key]
                data["requests"] += spider_stats.get("request_count", 0)
                data["responses"] += spider_stats.get("response_count", 0)
                data["items"] += spider_stats.get("item_scraped_count", 0)
                data["errors"] += spider_stats.get("error_count", 0)
                data["rpm"] += spider_stats.get("requests_per_minute", 0.0)

                latency = spider_stats.get("avg_latency_ms", 0.0)
                if latency > 0:
                    data["latency_sum"] += latency
                    data["latency_count"] += 1

        # 转换为列表（字段名与前端 SpiderStatsHistoryPoint 一致）
        result = []
        for time_key in sorted(time_data.keys()):
            data = time_data[time_key]
            avg_latency = (
                data["latency_sum"] / data["latency_count"]
                if data["latency_count"] > 0
                else 0.0
            )
            result.append({
                "timestamp": time_key,
                "requestCount": data["requests"],
                "responseCount": data["responses"],
                "itemScrapedCount": data["items"],
                "errorCount": data["errors"],
                "avgLatencyMs": round(avg_latency, 2),
                "requestsPerMinute": round(data["rpm"], 2),
            })

        return result

    @staticmethod
    def _extract_spider_stats(metrics: dict | None) -> dict | None:
        """从 metrics 中提取爬虫统计"""
        if not metrics:
            return None
        return metrics.get("spider_stats")


# 创建服务实例
spider_stats_service = SpiderStatsService()
