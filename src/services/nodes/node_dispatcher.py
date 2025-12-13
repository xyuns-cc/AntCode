"""节点任务分发器 - 智能负载均衡与项目同步

支持可选的 TaskQueueBackend 集成，用于 Master 端任务队列管理。
当启用时，任务会先入队到 Master 的队列，然后再分发到 Worker 节点。
"""
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING

import httpx
from loguru import logger

from src.models import Node, NodeStatus

# 使用 TYPE_CHECKING 避免循环导入
if TYPE_CHECKING:
    from src.services.scheduler.queue_backend import TaskQueueBackend, QueuedTask


class NodeLoadBalancer:
    """负载均衡器"""

    WEIGHT_CPU = 0.3
    WEIGHT_MEMORY = 0.3
    WEIGHT_TASKS = 0.25
    WEIGHT_LATENCY = 0.15

    MAX_CPU_THRESHOLD = 90
    MAX_MEMORY_THRESHOLD = 90
    MAX_TASKS_RATIO = 0.8

    def __init__(self):
        self._node_latencies: Dict[int, float] = {}
        self._latency_update_interval = 60
        self._last_latency_update: Dict[int, datetime] = {}

    def calculate_load_score(self, node: Node) -> float:
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

        if not node.metrics:
            return 100

        metrics = node.metrics

        # CPU 评分
        cpu_score = metrics.get("cpu", 100)

        # 内存评分
        memory_score = metrics.get("memory", 100)

        # 任务负载评分
        running_tasks = metrics.get("runningTasks", 0)
        max_tasks = metrics.get("maxConcurrentTasks", 5)
        task_score = (running_tasks / max_tasks) * 100 if max_tasks > 0 else 100

        # 网络延迟评分
        latency = self._node_latencies.get(node.id, 100)
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
            cpu_score * 0.30 +
            memory_score * 0.25 +
            task_score * 0.20 +
            latency_score * 0.15 +
            success_score * 0.10
        )

        return round(total_score, 2)

    def is_node_available(self, node: Node) -> bool:
        """检查节点可用性"""
        if node.status != NodeStatus.ONLINE:
            return False

        if not node.metrics:
            return False

        metrics = node.metrics

        if metrics.get("cpu", 100) >= self.MAX_CPU_THRESHOLD:
            return False

        if metrics.get("memory", 100) >= self.MAX_MEMORY_THRESHOLD:
            return False

        running_tasks = metrics.get("runningTasks", 0)
        max_tasks = metrics.get("maxConcurrentTasks", 5)
        if running_tasks >= max_tasks * self.MAX_TASKS_RATIO:
            return False

        return True

    async def update_node_latency(self, node: Node) -> float:
        """更新网络延迟"""
        now = datetime.now()
        last_update = self._last_latency_update.get(node.id)

        if last_update and (now - last_update).total_seconds() < self._latency_update_interval:
            return self._node_latencies.get(node.id, 100)

        try:
            start_time = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                response = await client.get(f"http://{node.host}:{node.port}/health")
            end_time = asyncio.get_event_loop().time()

            latency = (end_time - start_time) * 1000

            self._node_latencies[node.id] = latency
            self._last_latency_update[node.id] = now

            return latency
        except Exception:
            self._node_latencies[node.id] = 999
            return 999

    async def select_best_node(
        self,
        nodes: Optional[List[Node]] = None,
        exclude_nodes: Optional[List[int]] = None,
        region: Optional[str] = None,
        tags: Optional[List[str]] = None,
        require_render: bool = False,
    ) -> Optional[Node]:
        """
        选择最佳节点
        
        参数:
        - nodes: 候选节点列表（可选）
        - exclude_nodes: 排除的节点ID列表
        - region: 区域过滤
        - tags: 标签过滤
        - require_render: 是否需要渲染能力（DrissionPage）
        """
        if nodes is None:
            query = Node.filter(status=NodeStatus.ONLINE)
            if region:
                query = query.filter(region=region)
            nodes = await query.all()

        if not nodes:
            logger.warning("无可用节点")
            return None

        candidates = []
        for node in nodes:
            if exclude_nodes and node.id in exclude_nodes:
                continue

            if tags:
                node_tags = node.tags or []
                if not any(tag in node_tags for tag in tags):
                    continue

            # 检查渲染能力要求
            if require_render and not self._has_render_capability(node):
                logger.debug(f"节点 [{node.name}] 无渲染能力，跳过")
                continue

            if not self.is_node_available(node):
                logger.debug(f"节点不可用 [{node.name}]")
                continue

            await self.update_node_latency(node)

            candidates.append(node)

        if not candidates:
            if require_render:
                logger.warning("无符合条件的渲染节点")
            else:
                logger.warning("无符合条件节点")
            return None

        scored_nodes = []
        for node in candidates:
            score = self.calculate_load_score(node)
            scored_nodes.append((node, score))
            logger.debug(f"负载评分 [{node.name}] {score}")

        scored_nodes.sort(key=lambda x: x[1])

        best_node = scored_nodes[0][0]
        logger.info(f"选中节点 [{best_node.name}] 评分:{scored_nodes[0][1]}")

        return best_node

    def _has_render_capability(self, node: Node) -> bool:
        """检查节点是否有渲染能力"""
        if not node.capabilities:
            return False
        caps = node.capabilities
        cap = caps.get("drissionpage")
        return bool(cap and cap.get("enabled"))

    async def get_nodes_ranking(
        self,
        region: Optional[str] = None,
        top_n: int = 10,
    ) -> List[Dict[str, Any]]:
        """获取节点排名"""
        query = Node.filter(status=NodeStatus.ONLINE)
        if region:
            query = query.filter(region=region)

        nodes = await query.all()

        rankings = []
        for node in nodes:
            await self.update_node_latency(node)
            score = self.calculate_load_score(node)
            available = self.is_node_available(node)

            rankings.append({
                "node_id": node.public_id,
                "name": node.name,
                "host": node.host,
                "port": node.port,
                "region": node.region,
                "load_score": score,
                "available": available,
                "metrics": node.metrics,
                "latency_ms": self._node_latencies.get(node.id, -1),
            })

        rankings.sort(key=lambda x: x["load_score"])

        return rankings[:top_n]


class NodeTaskDispatcher:
    """任务分发器 - 支持批量任务和优先级调度
    
    支持可选的 TaskQueueBackend 集成：
    - 当 QUEUE_BACKEND=memory 或未设置时，使用内存队列
    - 当 QUEUE_BACKEND=redis 时，使用 Redis 队列（支持多 Master 共享）
    
    Requirements: 3.1-3.6, 4.1-4.7
    """

    # 项目类型到优先级的默认映射
    DEFAULT_PRIORITY_MAP = {
        "rule": 1,   # HIGH
        "code": 2,   # NORMAL
        "file": 2,   # NORMAL
    }

    def __init__(self):
        self.load_balancer = NodeLoadBalancer()
        self._pending_tasks: Dict[str, Dict] = {}

        # TaskQueueBackend 实例（延迟初始化）
        self._queue_backend: Optional["TaskQueueBackend"] = None
        self._queue_initialized = False

    async def init_queue_backend(self) -> None:
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
            from src.services.scheduler.queue_backend import get_queue_backend, get_queue_backend_type

            self._queue_backend = get_queue_backend()
            await self._queue_backend.start()
            self._queue_initialized = True

            backend_type = get_queue_backend_type()
            logger.info(f"任务队列后端已初始化: {backend_type}")
        except Exception as e:
            logger.error(f"初始化任务队列后端失败: {e}")
            raise

    async def shutdown_queue_backend(self) -> None:
        """
        关闭任务队列后端
        """
        if self._queue_backend and self._queue_initialized:
            await self._queue_backend.stop()
            self._queue_initialized = False
            logger.info("任务队列后端已关闭")

    def get_queue_backend(self) -> Optional["TaskQueueBackend"]:
        """
        获取任务队列后端实例
        
        Returns:
            TaskQueueBackend 实例，未初始化时返回 None
        """
        return self._queue_backend if self._queue_initialized else None

    async def get_master_queue_status(self) -> Dict[str, Any]:
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
        from src.services.scheduler.queue_backend import get_queue_backend_type

        status = await self._queue_backend.get_status()
        status["backend_type"] = get_queue_backend_type()
        status["initialized"] = True
        return status

    async def enqueue_task(
        self,
        task_id: str,
        project_id: str,
        priority: int,
        data: Dict[str, Any],
        project_type: str = "code",
    ) -> bool:
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

    async def dequeue_task(self, timeout: Optional[float] = None) -> Optional["QueuedTask"]:
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

    async def cancel_task_in_queue(self, task_id: str) -> bool:
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

    async def update_task_priority_in_queue(self, task_id: str, new_priority: int) -> bool:
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

    def task_in_queue(self, task_id: str) -> bool:
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
        project_id: str,
        execution_id: str,
        params: Optional[Dict] = None,
        environment_vars: Optional[Dict] = None,
        timeout: int = 3600,
        node_id: Optional[str] = None,
        region: Optional[str] = None,
        tags: Optional[List[str]] = None,
        priority: Optional[int] = None,
        project_type: str = "code",
        require_render: bool = False,
    ) -> Dict[str, Any]:
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
            node_id=node_id,
            region=region,
            tags=tags,
            require_render=require_render,
        )

        # 转换批量结果为单任务结果格式
        if result.get("success"):
            return {
                "success": True,
                "node_id": result.get("node_id"),
                "node_name": result.get("node_name"),
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
                "node_id": result.get("node_id"),
                "node_name": result.get("node_name"),
            }

    async def dispatch_batch(
        self,
        tasks: List[Dict[str, Any]],
        node_id: Optional[str] = None,
        region: Optional[str] = None,
        tags: Optional[List[str]] = None,
        batch_id: Optional[str] = None,
        require_render: bool = False,
    ) -> Dict[str, Any]:
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

        # 选择目标节点
        node = await self._select_node(node_id, region, tags, require_render=require_render)
        if not node:
            return {"success": False, "error": "无可用节点"}

        try:
            # 确保节点已连接到 Master（用于日志上报）
            connected = await self._ensure_node_connected(node)
            if not connected:
                return {"success": False, "error": f"节点未连接: {node.name}"}

            # 同步所有涉及的项目，并获取项目下载信息
            project_ids = list(set(t.get("project_id") for t in tasks if t.get("project_id")))
            sync_results, project_download_info = await self._sync_projects_to_node_with_info(node, project_ids)

            # 为每个任务添加项目下载信息（用于 Worker 端重新同步）
            enriched_tasks = []
            for task in tasks:
                task_copy = dict(task)
                pid = task.get("project_id")
                if pid and pid in project_download_info:
                    info = project_download_info[pid]
                    task_copy["download_url"] = info.get("download_url")
                    task_copy["api_key"] = node.api_key
                    task_copy["file_hash"] = info.get("file_hash")
                    task_copy["entry_point"] = info.get("entry_point")
                enriched_tasks.append(task_copy)

            # 发送批量任务到节点的优先级队列
            result = await self._send_batch_to_queue(
                node=node,
                tasks=enriched_tasks,
                batch_id=batch_id or str(uuid.uuid4()),
            )

            return {
                "success": result.get("success", False),
                "node_id": node.public_id,
                "node_name": node.name,
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
            logger.error(f"批量任务分发失败 [{node.name}] {e}")
            return {
                "success": False,
                "error": str(e),
                "node_id": node.public_id,
                "node_name": node.name,
            }

    async def _ensure_node_connected(self, node: Node) -> bool:
        """
        确保节点已连接到 Master（用于日志上报）
        
        向节点发送连接请求，使其建立与 Master 的 HTTP 连接
        """
        from src.core.config import settings

        node_url = f"http://{node.host}:{node.port}"
        master_url = settings.master_url

        try:
            machine_code = node.machine_code
            if not machine_code:
                # 尝试从节点实时获取机器码并回写
                async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                    resp = await client.get(f"{node_url}/node/info")
                    if resp.status_code == 200:
                        data = resp.json()
                        machine_code = data.get("machine_code")
                        if machine_code:
                            node.machine_code = machine_code
                            await node.save()
                            logger.info(f"已从节点同步机器码: {node.name} -> {machine_code}")
                    else:
                        logger.warning(f"获取节点机器码失败: HTTP {resp.status_code}")

            if not machine_code:
                logger.warning(f"节点缺少机器码，无法建立连接: {node.name}")
                return False

            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                # 发送连接请求（启用 WebSocket 优先）
                response = await client.post(
                    f"{node_url}/node/connect/v2",
                    json={
                        "machine_code": machine_code,
                        "api_key": node.api_key,
                        "master_url": master_url,
                        "node_id": node.public_id,
                        "secret_key": node.secret_key,
                        "use_websocket": True,  # 启用 WebSocket 优先模式
                    },
                    headers={"Authorization": f"Bearer {node.api_key}"}
                )

                if response.status_code == 200:
                    # 等待一小段时间让连接完全建立
                    await asyncio.sleep(0.5)
                    logger.info(f"节点 [{node.name}] 已连接到 Master: {master_url}")
                    return True
                else:
                    logger.warning(f"节点连接失败: HTTP {response.status_code}")
                    return False

        except Exception as e:
            logger.warning(f"确保节点连接失败: {e}")
            return False

    async def _select_node(
        self,
        node_id: Optional[str] = None,
        region: Optional[str] = None,
        tags: Optional[List[str]] = None,
        require_render: bool = False,
    ) -> Optional[Node]:
        """
        选择目标节点
        
        参数:
        - require_render: 是否需要渲染能力
        """
        if node_id:
            node = await Node.filter(public_id=node_id).first()
            if not node:
                try:
                    node = await Node.filter(id=int(node_id)).first()
                except ValueError:
                    pass

            if not node:
                logger.warning(f"节点不存在: {node_id}")
                return None

            if node.status != NodeStatus.ONLINE:
                logger.warning(f"节点离线: {node.name}")
                return None

            # 检查指定节点是否满足渲染要求
            if require_render and not self.load_balancer._has_render_capability(node):
                logger.warning(f"指定节点 [{node.name}] 无渲染能力")
                return None

            return node
        else:
            return await self.load_balancer.select_best_node(
                region=region, 
                tags=tags,
                require_render=require_render
            )

    async def _sync_projects_to_node(
        self,
        node: Node,
        project_ids: List[str],
    ) -> Dict[str, Any]:
        """批量同步项目到节点"""
        results, _ = await self._sync_projects_to_node_with_info(node, project_ids)
        return results

    async def _sync_projects_to_node_with_info(
        self,
        node: Node,
        project_ids: List[str],
    ) -> tuple:
        """批量同步项目到节点，并返回项目下载信息"""
        from src.models import Project
        from src.services.projects.project_sync_service import project_sync_service
        from src.services.nodes.node_project_service import node_project_service
        from src.core.config import settings

        results = {"synced": [], "skipped": [], "failed": []}
        project_download_info = {}  # {project_id: {download_url, file_hash, entry_point}}
        master_url = settings.master_url

        for project_id in project_ids:
            try:
                project = await Project.get_or_none(public_id=project_id)
                if not project:
                    results["failed"].append({"project_id": project_id, "reason": "项目不存在"})
                    continue

                transfer_info = await project_sync_service.get_project_transfer_info(project.id)
                current_hash = transfer_info.get("file_hash")

                # 保存项目下载信息（无论是否跳过同步）
                project_download_info[project_id] = {
                    "download_url": f"{master_url}/api/v1/projects/{project_id}/node-download",
                    "file_hash": current_hash,
                    "entry_point": transfer_info.get("entry_point"),
                }

                # 检查是否需要同步
                node_project = await node_project_service.check_node_has_project(
                    node_id=node.id,
                    project_public_id=project_id
                )

                if node_project and node_project.status == "synced" and node_project.file_hash == current_hash:
                    results["skipped"].append(project_id)
                    continue

                # 执行同步
                sync_success = await self._sync_single_project(node, project, transfer_info, current_hash)

                if sync_success:
                    await node_project_service.record_project_sync(
                        node_id=node.id,
                        project_id=project.id,
                        project_public_id=project_id,
                        file_hash=current_hash or "",
                        file_size=transfer_info.get("file_size", 0),
                        transfer_method=transfer_info["transfer_method"],
                    )
                    results["synced"].append(project_id)
                else:
                    results["failed"].append({"project_id": project_id, "reason": "同步失败"})

            except Exception as e:
                logger.error(f"同步项目 {project_id} 失败: {e}")
                results["failed"].append({"project_id": project_id, "reason": str(e)})

        return results, project_download_info

    async def _sync_single_project(
        self,
        node: Node,
        project,
        transfer_info: Dict,
        file_hash: str,
    ) -> bool:
        """同步单个项目到节点"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
                if transfer_info.get("transfer_method") == "code":
                    response = await client.post(
                        f"{node_url}/projects/code",
                        json={
                            "name": project.name,
                            "code_content": transfer_info["content"],
                            "language": transfer_info["language"],
                            "entry_point": transfer_info.get("entry_point"),
                            "master_project_id": project.public_id,
                        },
                        headers={"Authorization": f"Bearer {node.api_key}"}
                    )
                else:
                    from src.core.config import settings
                    master_url = settings.master_url

                    response = await client.post(
                        f"{node_url}/projects/sync-from-master",
                        json={
                            "project_id": project.public_id,
                            "name": project.name,
                            "download_url": f"{master_url}/api/v1/projects/{project.public_id}/node-download",
                            "description": project.description or "",
                            "entry_point": transfer_info.get("entry_point"),
                            "transfer_method": transfer_info["transfer_method"],
                            "file_hash": file_hash,
                            "file_size": transfer_info.get("file_size"),
                            "api_key": node.api_key,
                        },
                        headers={"Authorization": f"Bearer {node.api_key}"},
                    )

                return response.status_code == 200

        except Exception as e:
            logger.error(f"同步项目失败: {e}")
            return False

    async def _send_batch_to_queue(
        self,
        node: Node,
        tasks: List[Dict[str, Any]],
        batch_id: str,
    ) -> Dict[str, Any]:
        """发送批量任务到节点的优先级队列"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                response = await client.post(
                    f"{node_url}/queue/batch",
                    json={
                        "tasks": tasks,
                        "node_id": node.public_id,
                        "batch_id": batch_id,
                    },
                    headers={"Authorization": f"Bearer {node.api_key}"}
                )

                # 200 表示同步处理完成，202 表示异步处理已接受
                if response.status_code not in (200, 202):
                    error_detail = ""
                    try:
                        error_data = response.json()
                        error_detail = error_data.get("message") or error_data.get("detail") or ""
                    except Exception:
                        error_detail = response.text[:200] if response.text else ""
                    return {
                        "success": False,
                        "error": f"批量任务接收失败: HTTP{response.status_code} {error_detail}".strip()
                    }

                resp_json = response.json()
                data = resp_json.get("data", {})

                # 如果响应中没有 data 字段，尝试直接从响应中获取
                if not data and resp_json.get("success") is not None:
                    data = resp_json

                # 对于 202 响应，即使没有 accepted_count 也视为成功（异步处理）
                accepted_count = data.get("accepted_count")
                if accepted_count is None and response.status_code == 202:
                    accepted_count = len(tasks)  # 假设所有任务都被接受

                logger.debug(f"节点响应: status={response.status_code}, data={data}")

                return {
                    "success": True,
                    "batch_id": data.get("batch_id") or batch_id,
                    "accepted_count": accepted_count or len(tasks),
                    "rejected_count": data.get("rejected_count", 0),
                    "accepted_tasks": data.get("accepted_tasks", []),
                    "rejected_tasks": data.get("rejected_tasks", []),
                    "message": "批量任务已加入优先级队列",
                }

        except httpx.TimeoutException:
            return {"success": False, "error": "连接超时"}
        except httpx.ConnectError:
            return {"success": False, "error": "连接失败"}
        except Exception as e:
            logger.error(f"发送批量任务异常: {e}")
            return {"success": False, "error": str(e)}

    async def update_task_priority(
        self,
        node: Node,
        task_id: str,
        priority: int,
    ) -> Dict[str, Any]:
        """更新节点上任务的优先级"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.put(
                    f"{node_url}/queue/tasks/{task_id}/priority",
                    json={"priority": priority},
                    headers={"Authorization": f"Bearer {node.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json().get("data", {})
                    return {
                        "success": True,
                        "task_id": task_id,
                        "new_priority": data.get("new_priority"),
                        "new_position": data.get("new_position"),
                    }
                elif response.status_code == 404:
                    return {"success": False, "error": "任务不存在"}
                else:
                    return {"success": False, "error": f"更新失败: HTTP{response.status_code}"}

        except Exception as e:
            logger.error(f"更新任务优先级失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_queue_status(self, node: Node) -> Dict[str, Any]:
        """获取节点队列状态"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(
                    f"{node_url}/queue/status",
                    headers={"Authorization": f"Bearer {node.api_key}"}
                )

                if response.status_code == 200:
                    return response.json().get("data", {})
                return {}

        except Exception as e:
            logger.error(f"获取队列状态失败: {e}")
            return {}

    async def cancel_queued_task(
        self,
        node: Node,
        task_id: str,
    ) -> bool:
        """取消节点队列中的任务"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.delete(
                    f"{node_url}/queue/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {node.api_key}"}
                )
                return response.status_code == 200

        except Exception as e:
            logger.error(f"取消任务失败: {e}")
            return False

    async def sync_project_to_node(
        self,
        node: Node,
        project_id: str,
        project_data: Dict[str, Any],
    ) -> bool:
        """同步项目到节点"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            project_type = project_data.get("type")

            if project_type == "code":
                # 代码项目：直接传输代码内容
                async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                    response = await client.post(
                        f"{node_url}/projects/code",
                        json={
                            "name": project_data.get("name"),
                            "code_content": project_data.get("code_content", ""),
                            "language": project_data.get("language", "python"),
                            "description": project_data.get("description", ""),
                            "entry_point": project_data.get("entry_point"),
                            "master_project_id": project_id,
                        },
                        headers={"Authorization": f"Bearer {node.api_key}"}
                    )
                    return response.status_code == 200

            else:
                # 文件项目：让节点主动拉取
                from src.core.config import settings
                import os
                master_url = getattr(settings, 'MASTER_URL', None) or os.environ.get('MASTER_URL', f'http://localhost:{settings.SERVER_PORT}')
                download_url = f"{master_url}/api/v1/projects/{project_id}/download"

                # 使用节点专用下载接口
                from src.core.config import settings
                import os
                master_url = getattr(settings, 'MASTER_URL', None) or os.environ.get('MASTER_URL', f'http://localhost:{settings.SERVER_PORT}')
                node_download_url = f"{master_url}/api/v1/projects/{project_id}/node-download"

                async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                    response = await client.post(
                        f"{node_url}/projects/sync-from-master",
                        json={
                            "project_id": project_id,
                            "name": project_data.get("name"),
                            "download_url": node_download_url,
                            "description": project_data.get("description", ""),
                            "entry_point": project_data.get("entry_point"),
                            "transfer_method": project_data.get("transfer_method", "original"),
                            "file_hash": project_data.get("file_hash"),
                            "file_size": project_data.get("file_size"),
                            "api_key": node.api_key,
                        },
                        headers={"Authorization": f"Bearer {node.api_key}"}
                    )

                    if response.status_code == 200:
                        logger.info(f"项目 {project_id} 同步到节点 {node.name} 成功")
                        return True
                    else:
                        logger.error(f"节点响应错误: {response.status_code}")
                        return False

        except Exception as e:
            logger.error(f"同步项目到节点失败: {e}")
            return False

    async def get_task_status_from_node(
        self,
        node: Node,
        task_id: str,
    ) -> Optional[Dict[str, Any]]:
        """从节点获取任务状态"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(
                    f"{node_url}/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {node.api_key}"}
                )

                if response.status_code == 200:
                    return response.json().get("data")
                return None

        except Exception as e:
            logger.error(f"获取任务状态失败: {e}")
            return None

    async def get_task_logs_from_node(
        self,
        node: Node,
        task_id: str,
        log_type: str = "output",
        tail: int = 100,
    ) -> List[str]:
        """从节点获取任务日志"""
        node_url = f"http://{node.host}:{node.port}"

        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(
                    f"{node_url}/tasks/{task_id}/logs",
                    params={"log_type": log_type, "tail": tail},
                    headers={"Authorization": f"Bearer {node.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json().get("data", {})
                    return data.get("logs", [])
                return []

        except Exception as e:
            logger.error(f"获取任务日志失败: {e}")
            return []


# 全局实例
node_load_balancer = NodeLoadBalancer()
node_task_dispatcher = NodeTaskDispatcher()
