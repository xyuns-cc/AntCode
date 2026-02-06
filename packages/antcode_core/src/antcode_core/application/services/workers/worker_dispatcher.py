"""节点任务分发器 - 智能负载均衡与项目同步

支持可选的 TaskQueueBackend 集成，用于 Master 端任务队列管理。
当启用时，任务会先入队到 Master 的队列，然后再分发到 Worker 节点。
"""

import asyncio
import contextlib
from datetime import datetime

from loguru import logger

from antcode_core.domain.models import Worker, WorkerStatus


class WorkerLoadBalancer:
    """负载均衡器"""

    WEIGHT_CPU = 0.3
    WEIGHT_MEMORY = 0.3
    WEIGHT_TASKS = 0.25
    WEIGHT_LATENCY = 0.15

    MAX_CPU_THRESHOLD = 90
    MAX_MEMORY_THRESHOLD = 90
    MAX_TASKS_RATIO = 0.8

    def __init__(self):
        self._worker_latencies = {}
        self._latency_update_interval = 60
        self._last_latency_update = {}
        self._resource_cache = {}
        self._resource_cache_time = {}
        self._resource_cache_ttl = 2.0
        self._resource_lock = asyncio.Lock()
        self._resource_inflight = {}

    def _get_cached_resources(self, worker):
        cached = self._resource_cache.get(worker.id)
        if not cached:
            return None
        cached_at = self._resource_cache_time.get(worker.id)
        if cached_at is None:
            return None
        if (asyncio.get_event_loop().time() - cached_at) > self._resource_cache_ttl:
            return None
        return cached

    def _merge_metrics(self, worker_metrics, resource_metrics):
        merged = {}
        if worker_metrics:
            merged.update(worker_metrics)
        if resource_metrics:
            merged.update(resource_metrics)
        return merged

    async def _fetch_resources(self, worker):
        try:
            metrics = worker.metrics if isinstance(worker.metrics, dict) else {}

            # 如果数据库没有指标，尝试从 Redis 心跳中读取
            if not metrics:
                try:
                    from antcode_core.infrastructure.redis import get_redis_client

                    redis = await get_redis_client()
                    hb_key = f"antcode:heartbeat:{worker.public_id}"
                    raw = await redis.hgetall(hb_key)
                    metrics = {
                        (k.decode() if isinstance(k, bytes) else k): (
                            v.decode() if isinstance(v, bytes) else v
                        )
                        for k, v in raw.items()
                    }
                except Exception:
                    metrics = {}

            cpu = float(metrics.get("cpu") or metrics.get("cpu_percent") or 100)
            memory = float(metrics.get("memory") or metrics.get("memory_percent") or 100)
            disk = float(metrics.get("disk") or metrics.get("disk_percent") or 100)
            running_tasks = int(metrics.get("runningTasks") or metrics.get("running_tasks") or 0)
            max_concurrent = int(
                metrics.get("maxConcurrentTasks") or metrics.get("max_concurrent_tasks") or 1
            )
            queued_tasks = int(metrics.get("queuedTasks") or metrics.get("queued_tasks") or 0)

            normalized = {
                "cpu": cpu,
                "memory": memory,
                "disk": disk,
                "runningTasks": running_tasks,
                "maxConcurrentTasks": max_concurrent,
                "queuedTasks": queued_tasks,
            }

            self._resource_cache[worker.id] = normalized
            self._resource_cache_time[worker.id] = asyncio.get_event_loop().time()
            return normalized
        except Exception as e:
            logger.debug(f"资源查询失败: worker={worker.name}, error={e}")
            return None

    async def _refresh_resources(self, worker):
        cached = self._get_cached_resources(worker)
        if cached is not None:
            return cached

        async with self._resource_lock:
            cached = self._get_cached_resources(worker)
            if cached is not None:
                return cached
            inflight = self._resource_inflight.get(worker.id)
            if not inflight:
                inflight = asyncio.create_task(self._fetch_resources(worker))
                self._resource_inflight[worker.id] = inflight

        try:
            return await inflight
        finally:
            async with self._resource_lock:
                if self._resource_inflight.get(worker.id) is inflight:
                    self._resource_inflight.pop(worker.id, None)

    def calculate_load_score(self, worker, metrics=None):
        """
        计算负载评分（越低越优）

        评分因素:
        - CPU 使用率 (30%)
        - 内存使用率 (25%)
        - 任务负载 (20%)
        - 网络延迟 (15%)
        - 成功率 (10%)
        """
        import math

        if not metrics:
            return 100

        # CPU 评分
        cpu_score = metrics.get("cpu", 100)

        # 内存评分
        memory_score = metrics.get("memory", 100)

        # 任务负载评分
        running_tasks = metrics.get("runningTasks", 0)
        queued_tasks = metrics.get("queuedTasks", 0)
        max_tasks = metrics.get("maxConcurrentTasks", 5)
        task_load = running_tasks + queued_tasks
        task_score = (task_load / max_tasks) * 100 if max_tasks > 0 else 100
        if task_score > 100:
            task_score = 100

        # 网络延迟评分
        latency = self._worker_latencies.get(worker.id, 100)
        if latency <= 10:
            latency_score = 0
        elif latency >= 1000:
            latency_score = 100
        else:
            latency_score = min(100, max(0, 25 * math.log10(latency / 10)))

        # 成功率评分（从 metrics 中获取，如果有的话）
        success_rate = metrics.get("successRate", 100)  # 默认100%成功率
        # 成功率越高，分数越低
        success_score = 100 - success_rate

        # 综合评分（调整权重）
        total_score = (
            cpu_score * 0.30
            + memory_score * 0.25
            + task_score * 0.20
            + latency_score * 0.15
            + success_score * 0.10
        )

        return round(total_score, 2)

    def is_worker_available(self, worker, metrics=None):
        """检查节点可用性"""
        if worker.status != WorkerStatus.ONLINE:
            return False

        if metrics is None:
            metrics = self._get_cached_resources(worker)

        if not metrics:
            return False

        if metrics.get("cpu", 100) >= self.MAX_CPU_THRESHOLD:
            return False

        if metrics.get("memory", 100) >= self.MAX_MEMORY_THRESHOLD:
            return False

        running_tasks = metrics.get("runningTasks", 0)
        max_tasks = metrics.get("maxConcurrentTasks", 1)
        if max_tasks <= 0:
            return False
        return not running_tasks >= max_tasks * self.MAX_TASKS_RATIO

    async def update_worker_latency(self, worker):
        """更新网络延迟"""
        now = datetime.now()
        last_update = self._last_latency_update.get(worker.id)

        if last_update and (now - last_update).total_seconds() < self._latency_update_interval:
            return self._worker_latencies.get(worker.id, 100)

        try:
            if not worker.last_heartbeat:
                self._worker_latencies[worker.id] = 999
                return 999

            last_hb = worker.last_heartbeat
            if last_hb.tzinfo is not None:
                last_hb = last_hb.astimezone().replace(tzinfo=None)

            latency = int((now - last_hb).total_seconds() * 1000)
            if latency < 0:
                latency = 0

            self._worker_latencies[worker.id] = latency
            self._last_latency_update[worker.id] = now
            return latency
        except Exception:
            self._worker_latencies[worker.id] = 999
            return 999

    async def select_best_worker(
        self,
        workers=None,
        exclude_workers=None,
        region=None,
        tags=None,
        require_render=False,
    ):
        """
        选择最佳节点

        参数:
        - workers: 候选节点列表（可选）
        - exclude_workers: 排除的节点ID列表
        - region: 区域过滤
        - tags: 标签过滤
        - require_render: 是否需要渲染能力（DrissionPage）
        """
        if workers is None:
            query = Worker.filter(status=WorkerStatus.ONLINE.value)
            if region:
                query = query.filter(region=region)
            workers = await query.all()

        if not workers:
            logger.warning("无可用节点")
            return None

        filtered_workers = []
        for worker in workers:
            if exclude_workers and worker.id in exclude_workers:
                continue

            if tags:
                worker_tags = worker.tags or []
                if not any(tag in worker_tags for tag in tags):
                    continue

            # 检查渲染能力要求
            if require_render and not self._has_render_capability(worker):
                logger.debug(f"节点 [{worker.name}] 无渲染能力，跳过")
                continue

            filtered_workers.append(worker)

        if not filtered_workers:
            if require_render:
                logger.warning("无符合条件的渲染节点")
            else:
                logger.warning("无符合条件节点")
            return None

        resource_results = await asyncio.gather(
            *[self._refresh_resources(worker) for worker in filtered_workers],
            return_exceptions=True,
        )
        await asyncio.gather(
            *[self.update_worker_latency(worker) for worker in filtered_workers],
            return_exceptions=True,
        )

        candidates = []
        for worker, resource_metrics in zip(filtered_workers, resource_results, strict=False):
            if isinstance(resource_metrics, Exception) or not resource_metrics:
                logger.debug(f"资源信息不可用 [{worker.name}]")
                continue

            metrics = self._merge_metrics(worker.metrics, resource_metrics)
            if not self.is_worker_available(worker, metrics):
                logger.debug(f"节点不可用 [{worker.name}]")
                continue

            candidates.append((worker, metrics))

        if not candidates:
            if require_render:
                logger.warning("无符合条件的渲染节点")
            else:
                logger.warning("无符合条件节点")
            return None

        scored_workers = []
        for worker, metrics in candidates:
            score = self.calculate_load_score(worker, metrics)
            scored_workers.append((worker, score))
            logger.debug(f"负载评分 [{worker.name}] {score}")

        scored_workers.sort(key=lambda x: x[1])

        best_worker = scored_workers[0][0]
        logger.info(f"选中节点 [{best_worker.name}] 评分:{scored_workers[0][1]}")

        return best_worker

    def _has_render_capability(self, worker):
        """检查节点是否有渲染能力"""
        if not worker.capabilities:
            return False
        caps = worker.capabilities
        cap = caps.get("drissionpage")
        return bool(cap and cap.get("enabled"))

    async def get_workers_ranking(self, region=None, top_n=10):
        """获取节点排名"""
        query = Worker.filter(status=WorkerStatus.ONLINE.value)
        if region:
            query = query.filter(region=region)

        workers = await query.all()

        rankings = []
        resource_results = await asyncio.gather(
            *[self._refresh_resources(worker) for worker in workers],
            return_exceptions=True,
        )
        await asyncio.gather(
            *[self.update_worker_latency(worker) for worker in workers],
            return_exceptions=True,
        )

        for worker, resource_metrics in zip(workers, resource_results, strict=False):
            if isinstance(resource_metrics, Exception) or not resource_metrics:
                score = 100
                available = False
                metrics = {}
            else:
                metrics = self._merge_metrics(worker.metrics, resource_metrics)
                score = self.calculate_load_score(worker, metrics)
                available = self.is_worker_available(worker, metrics)

            rankings.append(
                {
                    "worker_id": worker.public_id,
                    "name": worker.name,
                    "host": worker.host,
                    "port": worker.port,
                    "region": worker.region,
                    "load_score": score,
                    "available": available,
                    "metrics": metrics,
                    "latency_ms": self._worker_latencies.get(worker.id, -1),
                }
            )

        rankings.sort(key=lambda x: x["load_score"])

        return rankings[:top_n]


class WorkerTaskDispatcher:
    """任务分发器 - 支持批量任务和优先级调度

    支持可选的 TaskQueueBackend 集成：
    - 当 QUEUE_BACKEND=memory 或未设置时，使用内存队列
    - 当 QUEUE_BACKEND=redis 时，使用 Redis 队列（支持多 Master 共享）

    Requirements: 3.1-3.6, 4.1-4.7
    """

    # 项目类型到优先级的默认映射
    DEFAULT_PRIORITY_MAP = {
        "rule": 1,  # 高优先级
        "code": 2,  # 普通优先级
        "file": 2,  # 普通优先级
    }

    def __init__(self):
        self.load_balancer = WorkerLoadBalancer()
        self._pending_tasks = {}

        # TaskQueueBackend 实例（延迟初始化）
        self._queue_backend = None
        self._queue_initialized = False

    async def init_queue_backend(self):
        """
        初始化任务队列后端

        根据 QUEUE_BACKEND 环境变量选择实现：
        - "memory" 或未设置: 使用 MemoryQueueBackend
        - "redis": 使用 RedisQueueBackend

        Requirements: 3.1, 3.2, 3.3
        """
        if self._queue_initialized:
            return

        try:
            # 延迟导入避免循环依赖
            from antcode_core.application.services.scheduler.queue_backend import (
                get_queue_backend,
                get_queue_backend_type,
            )

            self._queue_backend = get_queue_backend()
            await self._queue_backend.start()
            self._queue_initialized = True

            backend_type = get_queue_backend_type()
            logger.info(f"任务队列后端已初始化: {backend_type}")
        except Exception as e:
            logger.error(f"初始化任务队列后端失败: {e}")
            raise

    async def shutdown_queue_backend(self):
        """
        关闭任务队列后端
        """
        if self._queue_backend and self._queue_initialized:
            await self._queue_backend.stop()
            self._queue_initialized = False
            logger.info("任务队列后端已关闭")

    def get_queue_backend(self):
        """
        获取任务队列后端实例

        Returns:
            TaskQueueBackend 实例，未初始化时返回 None
        """
        return self._queue_backend if self._queue_initialized else None

    async def get_master_queue_status(self):
        """
        获取 Master 端任务队列状态

        Requirements: 3.1
        """
        if not self._queue_backend or not self._queue_initialized:
            return {
                "backend_type": "none",
                "initialized": False,
                "queue_depth": 0,
            }

        # 延迟导入避免循环依赖
        from antcode_core.application.services.scheduler.queue_backend import get_queue_backend_type

        status = await self._queue_backend.get_status()
        status["backend_type"] = get_queue_backend_type()
        status["initialized"] = True
        return status

    async def enqueue_task(
        self,
        task_id,
        project_id,
        priority,
        data,
        project_type="code",
    ):
        """
        将任务入队到 Master 端队列

        Args:
            task_id: 任务唯一标识
            project_id: 项目ID
            priority: 优先级（数值越小优先级越高）
            data: 任务数据
            project_type: 项目类型

        Returns:
            是否成功入队

        Requirements: 3.5
        """
        if not self._queue_backend or not self._queue_initialized:
            logger.warning("任务队列后端未初始化，无法入队")
            return False

        return await self._queue_backend.enqueue(
            task_id=task_id,
            project_id=project_id,
            priority=priority,
            data=data,
            project_type=project_type,
        )

    async def dequeue_task(self, timeout=None):
        """
        从 Master 端队列出队任务

        Args:
            timeout: 超时时间（秒）

        Returns:
            任务数据或 None

        Requirements: 3.6
        """
        if not self._queue_backend or not self._queue_initialized:
            return None

        return await self._queue_backend.dequeue(timeout=timeout)

    async def cancel_task_in_queue(self, task_id):
        """
        取消 Master 端队列中的任务

        Args:
            task_id: 任务唯一标识

        Returns:
            是否成功取消

        Requirements: 4.6
        """
        if not self._queue_backend or not self._queue_initialized:
            return False

        return await self._queue_backend.cancel(task_id)

    async def update_task_priority_in_queue(self, task_id, new_priority):
        """
        更新 Master 端队列中任务的优先级

        Args:
            task_id: 任务唯一标识
            new_priority: 新优先级

        Returns:
            是否成功更新

        Requirements: 4.7
        """
        if not self._queue_backend or not self._queue_initialized:
            return False

        return await self._queue_backend.update_priority(task_id, new_priority)

    def task_in_queue(self, task_id):
        """
        检查任务是否在 Master 端队列中

        Args:
            task_id: 任务唯一标识

        Returns:
            是否存在
        """
        if not self._queue_backend or not self._queue_initialized:
            return False

        return self._queue_backend.contains(task_id)

    async def dispatch_task(
        self,
        project_id,
        execution_id,
        params=None,
        environment_vars=None,
        timeout=3600,
        worker_id=None,
        region=None,
        tags=None,
        priority=None,
        project_type="code",
        require_render=False,
    ):
        """
        分发单个任务到节点（使用批量接口）

        参数:
        - require_render: 是否需要渲染能力（用于需要浏览器渲染的爬虫任务）
        """
        # 构建单任务批量请求
        task_item = {
            "task_id": execution_id,
            "project_id": project_id,
            "project_type": project_type,
            "priority": priority,
            "params": params or {},
            "environment": environment_vars or {},
            "timeout": timeout,
            "require_render": require_render,
        }

        result = await self.dispatch_batch(
            tasks=[task_item],
            worker_id=worker_id,
            region=region,
            tags=tags,
            require_render=require_render,
        )

        # 转换批量结果为单任务结果格式
        if result.get("success"):
            return {
                "success": True,
                "worker_id": result.get("worker_id"),
                "worker_name": result.get("worker_name"),
                "execution_id": execution_id,
                "task_id": execution_id,
                "message": "任务已分发到优先级队列",
                "transfer_skipped": result.get("transfer_skipped", False),
                "accepted_count": result.get("accepted_count", 0),
            }
        else:
            rejected = result.get("rejected_tasks", [])
            error_msg = result.get("error")
            if not error_msg and rejected:
                error_msg = rejected[0].get("reason", "任务被拒绝")
            if not error_msg:
                error_msg = "任务分发失败，未知原因"
            return {
                "success": False,
                "error": error_msg,
                "worker_id": result.get("worker_id"),
                "worker_name": result.get("worker_name"),
            }

    async def dispatch_batch(
        self,
        tasks,
        worker_id=None,
        region=None,
        tags=None,
        batch_id=None,
        require_render=False,
    ):
        """
        批量分发任务到节点（使用优先级队列接口）

        参数:
        - require_render: 是否需要渲染能力
        """
        import uuid

        if not tasks:
            return {"success": False, "error": "任务列表为空"}

        # 检查任务是否需要渲染能力
        if not require_render:
            for task in tasks:
                if task.get("require_render"):
                    require_render = True
                    break

        # 选择目标 Worker
        worker = await self._select_worker(worker_id, region, tags, require_render=require_render)
        if not worker:
            return {"success": False, "error": "无可用 Worker"}

        try:
            # 确保节点在线
            connected = await self._ensure_worker_connected(worker)
            if not connected:
                return {"success": False, "error": f"Worker 未在线: {worker.name}"}

            # 同步所有涉及的项目，并获取项目下载信息
            project_ids = list({t.get("project_id") for t in tasks if t.get("project_id")})
            (
                sync_results,
                project_download_info,
            ) = await self._sync_projects_to_worker_with_info(worker, project_ids)

            if sync_results.get("failed"):
                failed_items = sync_results.get("failed", [])
                reason = failed_items[0].get("reason") if failed_items else "项目同步失败"
                return {"success": False, "error": reason, "sync_results": sync_results}

            # 为每个任务添加项目下载信息（用于 Worker 端重新同步）
            enriched_tasks = []
            for task in tasks:
                task_copy = dict(task)
                pid = task.get("project_id")
                if pid and pid in project_download_info:
                    info = project_download_info[pid]
                    task_copy["file_hash"] = info.get("file_hash")
                    task_copy["entry_point"] = info.get("entry_point")
                    task_copy["download_url"] = info.get("download_url")
                    task_copy["is_compressed"] = info.get("is_compressed", True)
                enriched_tasks.append(task_copy)

            # 发送批量任务到节点的优先级队列
            result = await self._send_batch_to_queue(
                worker=worker,
                tasks=enriched_tasks,
                batch_id=batch_id or str(uuid.uuid4()),
            )

            return {
                "success": result.get("success", False),
                "worker_id": worker.public_id,
                "worker_name": worker.name,
                "batch_id": result.get("batch_id"),
                "accepted_count": result.get("accepted_count", 0),
                "rejected_count": result.get("rejected_count", 0),
                "accepted_tasks": result.get("accepted_tasks", []),
                "rejected_tasks": result.get("rejected_tasks", []),
                "message": result.get("message", "批量任务已分发"),
                "error": result.get("error"),
                "sync_results": sync_results,
            }

        except Exception as e:
            logger.error(f"批量任务分发失败 [{worker.name}] {e}")
            return {
                "success": False,
                "error": str(e),
                "worker_id": worker.public_id,
                "worker_name": worker.name,
            }

    async def _ensure_worker_connected(self, worker):
        """确保节点在线（依赖心跳状态）"""
        from antcode_core.application.services.workers.worker_heartbeat_service import (
            WorkerHeartbeatService,
        )

        if worker.status != WorkerStatus.ONLINE:
            return False
        if worker.last_heartbeat is None:
            return False

        timeout = WorkerHeartbeatService.HEARTBEAT_TIMEOUT
        now = datetime.now()
        last_hb = worker.last_heartbeat
        if last_hb.tzinfo is not None:
            last_hb = last_hb.astimezone().replace(tzinfo=None)
        return (now - last_hb).total_seconds() <= timeout

    async def _select_worker(
        self,
        worker_id=None,
        region=None,
        tags=None,
        require_render=False,
    ):
        """
        选择目标节点

        参数:
        - require_render: 是否需要渲染能力
        """
        if worker_id:
            worker = await Worker.filter(public_id=worker_id).first()
            if not worker:
                with contextlib.suppress(ValueError):
                    worker = await Worker.filter(id=int(worker_id)).first()

            if not worker:
                logger.warning(f"Worker 不存在: {worker_id}")
                return None

            if worker.status != WorkerStatus.ONLINE:
                logger.warning(f"节点离线: {worker.name}")
                return None

            # 检查指定 Worker 是否满足渲染要求
            if require_render and not self.load_balancer._has_render_capability(worker):
                logger.warning(f"指定 Worker [{worker.name}] 无渲染能力")
                return None

            return worker
        else:
            return await self.load_balancer.select_best_worker(
                region=region, tags=tags, require_render=require_render
            )

    async def _sync_projects_to_worker(self, worker, project_ids):
        """批量同步项目到节点"""
        from antcode_core.application.services.workers.worker_project_sync import worker_project_sync_service

        return await worker_project_sync_service.sync_projects_to_worker(worker, project_ids)

    async def _sync_projects_to_worker_with_info(self, worker, project_ids):
        """批量同步项目到节点，并返回项目下载信息"""
        from antcode_core.application.services.workers.worker_project_sync import worker_project_sync_service

        return await worker_project_sync_service.sync_projects_to_worker_with_info(worker, project_ids)

    async def _send_batch_to_queue(self, worker, tasks, batch_id):
        """写入 Redis Stream 分发批量任务"""
        from antcode_core.infrastructure.redis.streams import StreamClient

        stream = StreamClient()
        stream_key = f"antcode:task:ready:{worker.public_id}"

        messages = []
        for task in tasks:
            task_id = task.get("task_id", "")
            messages.append(
                {
                    "task_id": task_id,
                    "run_id": task.get("run_id") or task_id,
                    "project_id": task.get("project_id", ""),
                    "project_type": task.get("project_type", "code"),
                    "priority": task.get("priority") or 0,
                    "params": task.get("params") or {},
                    "environment": task.get("environment") or {},
                    "timeout": task.get("timeout", 3600),
                    "download_url": task.get("download_url") or "",
                    "file_hash": task.get("file_hash") or "",
                    "entry_point": task.get("entry_point") or "",
                    "is_compressed": task.get("is_compressed", True),
                }
            )

        try:
            await stream.xadd_batch(stream_key, messages)
        except Exception as e:
            logger.error(f"任务写入 Redis 失败: {e}")
            return {"success": False, "error": str(e)}

        accepted_tasks = [{"task_id": task.get("task_id")} for task in tasks]
        return {
            "success": True,
            "batch_id": batch_id,
            "accepted_count": len(accepted_tasks),
            "rejected_count": 0,
            "accepted_tasks": accepted_tasks,
            "rejected_tasks": [],
            "message": "批量任务已写入 Redis 队列",
        }

    async def update_task_priority(self, worker, task_id, priority):
        """更新节点上任务的优先级"""
        logger.warning("当前架构不支持更新节点队列任务优先级")
        return {"success": False, "error": "当前架构暂不支持该操作"}

    async def get_queue_status(self, worker):
        """获取节点队列状态（来自心跳指标）"""
        metrics = worker.metrics if isinstance(worker.metrics, dict) else {}
        return {
            "queued_tasks": metrics.get("queuedTasks") or metrics.get("queued_tasks", 0),
            "running_tasks": metrics.get("runningTasks") or metrics.get("running_tasks", 0),
            "max_concurrent_tasks": metrics.get("maxConcurrentTasks")
            or metrics.get("max_concurrent_tasks", 0),
        }

    async def cancel_queued_task(self, worker, task_id):
        """取消节点队列中的任务"""
        logger.warning("当前架构不支持取消节点队列任务")
        return False

    async def sync_project_to_worker(self, worker, project_id, project_data):
        """同步项目到节点"""
        from antcode_core.application.services.workers.worker_project_sync import worker_project_sync_service

        return await worker_project_sync_service.sync_project_to_worker(worker, project_id, project_data)

    async def get_task_status_from_worker(self, worker, task_id):
        """从节点获取任务状态"""
        try:
            from antcode_core.domain.models.task_run import TaskRun

            execution = await TaskRun.get_or_none(execution_id=str(task_id))
            if not execution:
                execution = await TaskRun.get_or_none(public_id=str(task_id))
            if not execution:
                return None

            return {
                "execution_id": execution.execution_id,
                "status": execution.status,
                "start_time": execution.start_time.isoformat()
                if execution.start_time
                else None,
                "end_time": execution.end_time.isoformat() if execution.end_time else None,
                "exit_code": execution.exit_code,
                "error_message": execution.error_message,
            }
        except Exception as e:
            logger.error(f"获取任务状态失败: {e}")
            return None

    async def get_task_logs_from_worker(self, worker, task_id, log_type="output", tail=100):
        """从节点获取任务日志"""
        try:
            from antcode_core.application.services.workers.distributed_log_service import (
                distributed_log_service,
            )

            log_type = "stdout" if log_type == "output" else "stderr" if log_type == "error" else log_type
            return await distributed_log_service.get_logs(
                execution_id=str(task_id),
                log_type=log_type,
                tail=tail,
            )
        except Exception as e:
            logger.error(f"获取任务日志失败: {e}")
            return []


# 全局实例
worker_load_balancer = WorkerLoadBalancer()
worker_task_dispatcher = WorkerTaskDispatcher()
