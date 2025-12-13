"""节点服务层 - 管理分布式工作节点"""

import ipaddress
import os
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from loguru import logger
from tortoise.expressions import Q

from src.models import Node, NodeHeartbeat, NodeStatus
from src.schemas.node import (
    NodeCreateRequest, NodeUpdateRequest, NodeMetrics,
    NodeAggregateStats, NodeRegisterRequest, NodeConnectRequest
)


def is_safe_host(host: str) -> bool:
    """
    检查主机地址是否安全（防止 SSRF 攻击）
    
    禁止访问:
    - 私有 IP 地址 (10.x.x.x, 172.16-31.x.x, 192.168.x.x)
    - 回环地址 (127.x.x.x, localhost)
    - 链路本地地址 (169.254.x.x)
    - 元数据服务地址 (169.254.169.254)
    
    可通过环境变量 ALLOW_PRIVATE_NODES=true 允许私有地址（仅限内网部署）
    """
    allow_private = os.getenv("ALLOW_PRIVATE_NODES", "false").lower() == "true"
    
    # 解析主机名
    try:
        # 如果是域名，尝试解析
        import socket
        ip_str = socket.gethostbyname(host)
        ip = ipaddress.ip_address(ip_str)
    except (socket.gaierror, ValueError):
        # 无法解析的主机名，拒绝
        return False
    
    # 检查是否为危险地址
    if ip.is_loopback:
        return allow_private  # 回环地址
    
    if ip.is_private:
        return allow_private  # 私有地址
    
    if ip.is_link_local:
        return False  # 链路本地地址，始终禁止
    
    if ip.is_reserved:
        return False  # 保留地址，始终禁止
    
    # 特别检查云元数据服务地址
    metadata_ips = ["169.254.169.254", "fd00:ec2::254"]
    if str(ip) in metadata_ips:
        return False
    
    return True


class NodeService:
    """节点服务类"""

    # 心跳超时时间（秒）
    HEARTBEAT_TIMEOUT = 60

    # 智能心跳检测配置
    HEARTBEAT_INTERVAL_ONLINE = 3      # 在线节点检测间隔（秒）
    HEARTBEAT_INTERVAL_OFFLINE = 60    # 离线节点检测间隔（秒）
    HEARTBEAT_MAX_FAILURES = 5         # 最大失败次数，超过后停止自动检测
    HEARTBEAT_TIMEOUT_REQUEST = 2      # HTTP请求超时时间（秒）

    def __init__(self):
        """初始化节点服务"""
        # 节点缓存：{node_id: node_object}
        self._node_cache: Dict[int, Node] = {}

        # 节点状态：{node_id: {'failures': int, 'next_check': datetime, 'suspended': bool}}
        self._node_states: Dict[int, Dict[str, Any]] = {}

        # 缓存更新时间
        self._cache_updated_at: Optional[datetime] = None

        # 缓存有效期（秒）
        self._cache_ttl = 300  # 5分钟

    async def init_heartbeat_cache(self):
        """初始化心跳检测缓存"""
        try:
            nodes = await Node.all()
            now = datetime.now()

            self._node_cache.clear()
            self._node_states.clear()

            for node in nodes:
                self._node_cache[node.id] = node
                self._node_states[node.id] = {
                    'failures': 0,
                    'next_check': now,  # 立即检测
                    'suspended': False
                }

            self._cache_updated_at = now
            logger.info(f"心跳检测缓存已初始化，共 {len(nodes)} 个节点")
        except Exception as e:
            logger.error(f"初始化心跳缓存失败: {e}")

    async def refresh_node_cache(self):
        """刷新节点缓存（如果过期）"""
        now = datetime.now()

        # 如果缓存不存在或已过期，重新加载
        if not self._cache_updated_at or \
           (now - self._cache_updated_at).total_seconds() > self._cache_ttl:
            nodes = await Node.all()

            # 更新现有节点，添加新节点
            for node in nodes:
                if node.id not in self._node_cache:
                    # 新节点
                    self._node_cache[node.id] = node
                    self._node_states[node.id] = {
                        'failures': 0,
                        'next_check': now,
                        'suspended': False
                    }
                else:
                    # 更新现有节点
                    self._node_cache[node.id] = node

            # 移除已删除的节点
            cached_ids = set(self._node_cache.keys())
            current_ids = {n.id for n in nodes}
            deleted_ids = cached_ids - current_ids

            for node_id in deleted_ids:
                del self._node_cache[node_id]
                del self._node_states[node_id]

            self._cache_updated_at = now

    async def init_node_secrets(self):
        """初始化时加载所有节点密钥到验证器"""
        from src.core.security.node_auth import node_auth_verifier

        nodes = await Node.filter(secret_key__isnull=False).all()
        for node in nodes:
            if node.secret_key:
                node_auth_verifier.register_node_secret(node.public_id, node.secret_key)

        logger.info(f"已加载 {len(nodes)} 个节点密钥")

    async def get_nodes(
        self,
        page: int = 1,
        size: int = 20,
        status_filter: Optional[str] = None,
        region: Optional[str] = None,
        search: Optional[str] = None
    ) -> Tuple[List[Node], int]:
        """获取节点列表"""
        query = Node.all()

        if status_filter:
            query = query.filter(status=status_filter)

        if region:
            query = query.filter(region=region)

        if search:
            query = query.filter(
                Q(name__icontains=search) |
                Q(host__icontains=search) |
                Q(description__icontains=search)
            )

        total = await query.count()
        offset = (page - 1) * size
        nodes = await query.order_by('-created_at').offset(offset).limit(size)

        # 检查并更新离线节点状态
        await self._check_offline_nodes(nodes)

        return nodes, total

    async def get_all_nodes(self) -> List[Node]:
        """获取所有节点（不分页）"""
        nodes = await Node.all().order_by('-created_at')
        await self._check_offline_nodes(nodes)
        return nodes

    async def get_node_by_id(self, node_id) -> Optional[Node]:
        """根据ID获取节点"""
        # 尝试作为 public_id
        node = await Node.filter(public_id=str(node_id)).first()
        if node:
            return node

        # 尝试作为内部 ID
        try:
            internal_id = int(node_id)
            return await Node.filter(id=internal_id).first()
        except (ValueError, TypeError):
            return None

    async def create_node(
        self,
        request: NodeCreateRequest,
        user_id: Optional[int] = None
    ) -> Node:
        """创建节点"""
        # 检查名称是否已存在
        existing = await Node.filter(name=request.name).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="节点名称已存在"
            )

        # 检查地址是否已存在
        existing = await Node.filter(host=request.host, port=request.port).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该地址已被其他节点使用"
            )

        # 生成 API 密钥
        api_key = secrets.token_hex(32)
        secret_key = secrets.token_hex(64)

        node = await Node.create(
            name=request.name,
            host=request.host,
            port=request.port,
            region=request.region,
            description=request.description,
            tags=request.tags or [],
            status=NodeStatus.OFFLINE,
            api_key=api_key,
            secret_key=secret_key,
            created_by=user_id
        )

        logger.info(f"节点创建成功: {node.name} ({node.host}:{node.port})")
        return node

    async def update_node(
        self,
        node_id,
        request: NodeUpdateRequest
    ) -> Optional[Node]:
        """更新节点"""
        node = await self.get_node_by_id(node_id)
        if not node:
            return None

        update_data = request.dict(exclude_unset=True)

        # 检查名称唯一性
        if 'name' in update_data and update_data['name'] != node.name:
            existing = await Node.filter(name=update_data['name']).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="节点名称已存在"
                )

        # 检查地址唯一性
        new_host = update_data.get('host', node.host)
        new_port = update_data.get('port', node.port)
        if new_host != node.host or new_port != node.port:
            existing = await Node.filter(host=new_host, port=new_port).exclude(id=node.id).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该地址已被其他节点使用"
                )

        await node.update_from_dict(update_data)
        await node.save()

        logger.info(f"节点更新成功: {node.name}")
        return node

    async def rebind_node(
        self,
        node_id: str,
        new_machine_code: str,
        verify_connection: bool = True
    ) -> Dict[str, Any]:
        """
        重新绑定节点机器码
        
        当节点重启后机器码变化时，使用此方法更新机器码而无需删除重建节点
        
        参数:
        - node_id: 节点公开ID
        - new_machine_code: 新的机器码
        - verify_connection: 是否验证新机器码与节点匹配
        
        返回:
        - success: 是否成功
        - node: 更新后的节点对象
        - error: 错误信息（如果失败）
        """
        node = await self.get_node_by_id(node_id)
        if not node:
            return {"success": False, "error": "节点不存在"}

        old_machine_code = getattr(node, "machine_code", None)
        if not old_machine_code:
            # 若旧节点缺少机器码，要求强制验证连接并写入新机器码
            verify_connection = True

        # 如果需要验证连接
        if verify_connection:
            try:
                async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                    response = await client.get(f"http://{node.host}:{node.port}/health")
                    if response.status_code == 200:
                        data = response.json()
                        actual_code = data.get("machine_code", "")

                        if actual_code != new_machine_code:
                            return {
                                "success": False,
                                "error": f"机器码不匹配: 期望 {new_machine_code}, 实际 {actual_code}"
                            }
                    else:
                        return {
                            "success": False,
                            "error": f"节点健康检查失败: HTTP {response.status_code}"
                        }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"无法连接到节点: {str(e)}"
                }

        # 更新机器码
        node.machine_code = new_machine_code
        node.status = NodeStatus.ONLINE  # 重新绑定后设为在线
        node.last_heartbeat = datetime.now()
        await node.save()

        logger.info(f"节点机器码已更新: {node.name}, {old_machine_code} -> {new_machine_code}")

        return {"success": True, "node": node}

    async def delete_node(self, node_id) -> bool:
        """删除节点（级联删除所有关联数据）"""
        node = await self.get_node_by_id(node_id)
        if not node:
            return False

        # 级联删除所有关联数据
        deleted_counts = await self._cascade_delete_node_data(node.id, node.public_id)

        # 删除节点
        await node.delete()

        logger.info(f"节点删除成功: {node.name}, 级联删除: {deleted_counts}")
        return True

    async def _cascade_delete_node_data(self, node_internal_id: int, node_public_id: str) -> dict:
        """级联删除节点的所有关联数据"""
        from src.models import (
            UserNodePermission, Venv, ProjectVenvBinding,
            NodeProject, NodeProjectFile
        )
        from src.models.scheduler import ScheduledTask, TaskExecution
        from src.models.monitoring import NodePerformanceHistory, SpiderMetricsHistory, NodeEvent

        deleted = {
            "heartbeats": 0,
            "permissions": 0,
            "venvs": 0,
            "venv_bindings": 0,
            "node_projects": 0,
            "node_project_files": 0,
            "task_executions": 0,
            "tasks": 0,
            "performance_history": 0,
            "spider_metrics": 0,
            "events": 0,
        }

        try:
            # 1. 删除心跳记录
            deleted["heartbeats"] = await NodeHeartbeat.filter(node_id=node_internal_id).delete()

            # 2. 删除用户节点权限
            deleted["permissions"] = await UserNodePermission.filter(node_id=node_internal_id).delete()

            # 3. 删除节点上的虚拟环境及其绑定
            venvs = await Venv.filter(node_id=node_internal_id).all()
            if venvs:
                venv_ids = [v.id for v in venvs]
                deleted["venv_bindings"] = await ProjectVenvBinding.filter(venv_id__in=venv_ids).delete()
                deleted["venvs"] = await Venv.filter(id__in=venv_ids).delete()

            # 4. 删除节点项目绑定
            node_projects = await NodeProject.filter(node_id=node_internal_id).all()
            if node_projects:
                np_ids = [np.id for np in node_projects]
                deleted["node_project_files"] = await NodeProjectFile.filter(node_project_id__in=np_ids).delete()
                deleted["node_projects"] = await NodeProject.filter(id__in=np_ids).delete()

            # 5. 删除节点上的任务及执行记录
            tasks = await ScheduledTask.filter(node_id=node_internal_id).all()
            if tasks:
                task_ids = [t.id for t in tasks]
                deleted["task_executions"] = await TaskExecution.filter(task_id__in=task_ids).delete()
                deleted["tasks"] = await ScheduledTask.filter(id__in=task_ids).delete()

            # 6. 删除监控数据（使用 public_id，因为监控表的 node_id 是字符串）
            deleted["performance_history"] = await NodePerformanceHistory.filter(node_id=node_public_id).delete()
            deleted["spider_metrics"] = await SpiderMetricsHistory.filter(node_id=node_public_id).delete()
            deleted["events"] = await NodeEvent.filter(node_id=node_public_id).delete()

        except Exception as e:
            logger.error(f"级联删除节点数据失败: {e}")
            raise

        return deleted

    async def batch_delete_nodes(self, node_ids: List[str]) -> Dict[str, Any]:
        """批量删除节点（级联删除所有关联数据）"""
        # 解析节点ID（支持public_id和内部ID混合）
        nodes_to_delete = []
        for node_id in node_ids:
            node = await self.get_node_by_id(node_id)
            if node:
                nodes_to_delete.append(node)

        if not nodes_to_delete:
            return {
                "success_count": 0,
                "failed_count": len(node_ids),
                "failed_ids": node_ids
            }

        # 级联删除每个节点的关联数据
        total_deleted = {}
        for node in nodes_to_delete:
            try:
                deleted = await self._cascade_delete_node_data(node.id, node.public_id)
                for key, count in deleted.items():
                    total_deleted[key] = total_deleted.get(key, 0) + count
            except Exception as e:
                logger.error(f"级联删除节点 {node.name} 数据失败: {e}")

        # 批量删除节点
        internal_ids = [n.id for n in nodes_to_delete]
        deleted_count = await Node.filter(id__in=internal_ids).delete()

        success_ids = [n.public_id for n in nodes_to_delete]
        failed_ids = list(set(node_ids) - set(success_ids))

        logger.info(f"批量删除节点: 成功{deleted_count}个, 级联删除: {total_deleted}")

        return {
            "success_count": deleted_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids
        }

    async def register_node(self, request: NodeRegisterRequest) -> Tuple[Node, str, str]:
        """节点自注册"""
        from src.core.security.node_auth import node_auth_verifier

        # 检查是否已存在
        existing = await Node.filter(host=request.host, port=request.port).first()
        if existing:
            # 更新现有节点
            existing.name = request.name
            existing.region = request.region
            existing.version = request.version
            existing.status = NodeStatus.ONLINE
            existing.last_heartbeat = datetime.now()
            if request.metrics:
                existing.metrics = request.metrics.model_dump()
            await existing.save()

            # 注册密钥到验证器
            if existing.secret_key:
                node_auth_verifier.register_node_secret(existing.public_id, existing.secret_key)

            return existing, existing.api_key, existing.secret_key

        # 创建新节点
        api_key = secrets.token_hex(32)
        secret_key = secrets.token_hex(64)

        node = await Node.create(
            name=request.name,
            host=request.host,
            port=request.port,
            region=request.region,
            version=request.version,
            status=NodeStatus.ONLINE,
            api_key=api_key,
            secret_key=secret_key,
            last_heartbeat=datetime.now(timezone.utc),
            metrics=request.metrics.model_dump() if request.metrics else None
        )

        # 注册密钥到验证器
        node_auth_verifier.register_node_secret(node.public_id, secret_key)

        logger.info(f"节点注册成功: {node.name} ({node.host}:{node.port})")
        return node, api_key, secret_key

    async def heartbeat(
        self,
        node_id: str,
        api_key: str,
        status_value: str = NodeStatus.ONLINE,
        metrics: Optional[NodeMetrics] = None,
        version: Optional[str] = None,
        # 操作系统信息
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        python_version: Optional[str] = None,
        machine_arch: Optional[str] = None,
        # 节点能力
        capabilities: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """处理节点心跳"""
        node = await self.get_node_by_id(node_id)
        if not node:
            logger.warning(f"心跳失败: 节点不存在 {node_id}")
            return False

        # 验证 API 密钥
        if node.api_key != api_key:
            logger.warning(f"心跳失败: API密钥不匹配 {node_id}")
            return False

        # 更新节点状态
        node.status = status_value
        node.last_heartbeat = datetime.now()
        if metrics:
            node.metrics = metrics.model_dump()
        if version:
            node.version = version

        # 更新操作系统信息（如果提供）
        if os_type:
            node.os_type = os_type
        if os_version:
            node.os_version = os_version
        if python_version:
            node.python_version = python_version
        if machine_arch:
            node.machine_arch = machine_arch

        # 更新节点能力（如果提供）
        if capabilities:
            node.capabilities = capabilities
            # 记录能力变更
            has_render = self._check_render_capability(capabilities)
            logger.info(f"节点 {node.name} 能力更新: 渲染能力={has_render}")

        await node.save()

        # 记录心跳历史
        await NodeHeartbeat.create(
            node_id=node.id,
            status=status_value,
            metrics=metrics.model_dump() if metrics else None
        )

        return True

    def _check_render_capability(self, capabilities: Dict[str, Any]) -> bool:
        """检查节点是否有渲染能力"""
        if not capabilities:
            return False
        cap = capabilities.get("drissionpage")
        return bool(cap and cap.get("enabled"))

    async def test_connection(self, node_id) -> Dict[str, Any]:
        """
        手动测试节点连接
        成功时会恢复自动心跳检测
        """
        node = await self.get_node_by_id(node_id)
        if not node:
            return {"success": False, "error": "节点不存在"}

        url = f"http://{node.host}:{node.port}/health"
        logger.info(f"手动测试节点连接: {node.name} ({url})")

        try:
            start_time = asyncio.get_event_loop().time()
            # trust_env=False 禁用系统代理，避免被本地代理拦截
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(url)
            end_time = asyncio.get_event_loop().time()

            latency = int((end_time - start_time) * 1000)
            logger.info(f"节点 {node.name} 响应: HTTP {response.status_code}, 延迟 {latency}ms")

            if response.status_code == 200:
                # 使用智能心跳检测的manual_test_node恢复自动检测
                is_online = await self.manual_test_node(node.id)

                if is_online:
                    logger.info(f"节点 {node.name} 测试成功，已恢复自动心跳检测")
                    return {"success": True, "latency": latency}
                else:
                    return {"success": False, "error": "恢复自动检测失败"}
            else:
                logger.warning(f"节点 {node.name} 连接失败: HTTP {response.status_code}")
                return {"success": False, "error": f"HTTP {response.status_code}"}
        except httpx.TimeoutException:
            logger.warning(f"节点 {node.name} 连接超时")
            return {"success": False, "error": "连接超时"}
        except httpx.ConnectError as e:
            logger.warning(f"节点 {node.name} 无法连接: {e}")
            return {"success": False, "error": "无法连接到节点"}
        except Exception as e:
            logger.error(f"节点 {node.name} 测试连接异常: {e}")
            return {"success": False, "error": str(e)}

    async def connect_node(
        self,
        request: NodeConnectRequest,
        master_url: str,
        user_id: Optional[int] = None
    ) -> Node:
        """
        通过地址和机器码连接节点
        1. 向节点发送请求获取信息
        2. 验证机器码
        3. 创建节点记录
        4. 通知节点已连接
        """
        # SSRF 防护：验证目标地址安全性
        if not is_safe_host(request.host):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不允许连接到该地址（私有/保留地址）"
            )

        node_url = f"http://{request.host}:{request.port}"

        # 1. 获取节点信息
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(f"{node_url}/node/info")

                if response.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"无法连接到节点: HTTP {response.status_code}"
                    )

                node_info = response.json()
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="连接节点超时"
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="无法连接到节点，请检查地址和端口"
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"连接节点失败: {str(e)}"
            )

        # 2. 验证机器码
        reported_code = node_info.get("machine_code")
        if not reported_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="节点未上报机器码，无法绑定"
            )
        if reported_code != request.machine_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="机器码不匹配"
            )

        # 3. 检查节点是否已存在（按地址匹配）
        existing = await Node.filter(
            host=request.host, 
            port=request.port
        ).first()

        if existing:
            # 更新现有节点（不更新名称，避免冲突）
            existing.region = node_info.get("region") or existing.region
            existing.version = node_info.get("version")
            existing.machine_code = node_info.get("machine_code") or existing.machine_code
            existing.status = NodeStatus.ONLINE
            existing.last_heartbeat = datetime.now()
            if node_info.get("metrics"):
                existing.metrics = {**(existing.metrics or {}), **node_info.get("metrics", {})}
            else:
                # 确保机器码持久化在 metrics 中
                if not isinstance(existing.metrics, dict):
                    existing.metrics = {}
                if node_info.get("machine_code"):
                    existing.metrics["machine_code"] = node_info.get("machine_code")
            await existing.save()
            node = existing
            logger.info(f"节点重新连接: {node.name} ({node.host}:{node.port})")
        else:
            # 创建新节点
            api_key = secrets.token_hex(32)
            secret_key = secrets.token_hex(64)

            # 检查名称是否已存在，如果存在则生成唯一名称
            node_name = node_info.get("name", f"Node-{request.host}")
            name_exists = await Node.filter(name=node_name).exists()
            if name_exists:
                # 生成唯一名称：原名称 + 端口号
                node_name = f"{node_name}-{request.port}"
                # 如果还是重复，再加时间戳
                if await Node.filter(name=node_name).exists():
                    import time
                    node_name = f"{node_info.get('name', 'Node')}-{int(time.time())}"

            node = await Node.create(
                name=node_name,
                host=request.host,
                port=request.port,
                region=node_info.get("region"),
                version=node_info.get("version"),
                status=NodeStatus.ONLINE,
                api_key=api_key,
                secret_key=secret_key,
                last_heartbeat=datetime.now(timezone.utc),
                metrics={**(node_info.get("metrics") or {}), "machine_code": node_info.get("machine_code")},
                created_by=user_id
            )
            logger.info(f"节点连接成功: {node.name} ({node.host}:{node.port})")

        # 4. 通知节点已连接（使用 v2 API 传递 node_id）
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                await client.post(
                    f"{node_url}/node/connect/v2",
                    json={
                        "machine_code": request.machine_code,
                        "api_key": node.api_key,
                        "master_url": master_url,
                        "node_id": node.public_id,
                        "secret_key": node.secret_key,
                        "use_websocket": False,  # 暂时使用 HTTP
                    }
                )
        except Exception as e:
            logger.warning(f"通知节点连接状态失败: {e}")

        return node

    async def disconnect_node(self, node_id) -> bool:
        """断开节点连接"""
        node = await self.get_node_by_id(node_id)
        if not node:
            return False

        # 通知节点断开
        node_url = f"http://{node.host}:{node.port}"
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                await client.post(
                    f"{node_url}/node/disconnect",
                    json={"machine_code": node.api_key[:16] if node.api_key else ""}
                )
        except Exception as e:
            logger.warning(f"通知节点断开失败: {e}")

        # 更新节点状态
        node.status = NodeStatus.OFFLINE
        await node.save()

        logger.info(f"节点已断开: {node.name}")
        return True

    async def get_aggregate_stats(self) -> NodeAggregateStats:
        """获取节点聚合统计"""
        nodes = await Node.all()

        total_nodes = len(nodes)
        online_nodes = sum(1 for n in nodes if n.status == NodeStatus.ONLINE)
        offline_nodes = sum(1 for n in nodes if n.status == NodeStatus.OFFLINE)
        maintenance_nodes = sum(1 for n in nodes if n.status == NodeStatus.MAINTENANCE)

        total_projects = 0
        total_tasks = 0
        running_tasks = 0
        total_envs = 0
        total_cpu = 0
        total_memory = 0
        nodes_with_metrics = 0

        for node in nodes:
            if node.metrics:
                metrics = node.metrics
                total_projects += metrics.get('projectCount', 0)
                total_tasks += metrics.get('taskCount', 0)
                running_tasks += metrics.get('runningTasks', 0)
                total_envs += metrics.get('envCount', 0)
                total_cpu += metrics.get('cpu', 0)
                total_memory += metrics.get('memory', 0)
                nodes_with_metrics += 1

        avg_cpu = total_cpu / nodes_with_metrics if nodes_with_metrics > 0 else 0
        avg_memory = total_memory / nodes_with_metrics if nodes_with_metrics > 0 else 0

        return NodeAggregateStats(
            totalNodes=total_nodes,
            onlineNodes=online_nodes,
            offlineNodes=offline_nodes,
            maintenanceNodes=maintenance_nodes,
            totalProjects=total_projects,
            totalTasks=total_tasks,
            runningTasks=running_tasks,
            totalEnvs=total_envs,
            avgCpu=round(avg_cpu, 1),
            avgMemory=round(avg_memory, 1)
        )

    async def _check_offline_nodes(self, nodes: List[Node]) -> None:
        """检查并更新离线节点"""
        # 使用本地时间（naive datetime）避免时区问题
        now = datetime.now()
        timeout = timedelta(seconds=self.HEARTBEAT_TIMEOUT)

        for node in nodes:
            if node.status == NodeStatus.ONLINE and node.last_heartbeat:
                # 将心跳时间转换为 naive datetime（去掉时区信息）
                last_hb = node.last_heartbeat
                if last_hb.tzinfo is not None:
                    # 如果有时区信息，转换为本地时间再去掉时区
                    last_hb = last_hb.astimezone().replace(tzinfo=None)

                time_diff = now - last_hb
                if time_diff > timeout:
                    logger.info(f"节点 {node.name} 心跳超时 ({time_diff.total_seconds():.0f}秒 > {self.HEARTBEAT_TIMEOUT}秒)，标记为离线")
                    node.status = NodeStatus.OFFLINE
                    await node.save()

    async def refresh_node_status(self, node_id) -> Optional[Node]:
        """刷新节点状态，并尝试重新连接未连接的节点"""
        node = await self.get_node_by_id(node_id)
        if not node:
            return None

        node_url = f"http://{node.host}:{node.port}"

        # 测试连接并获取节点信息
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(f"{node_url}/health")

                if response.status_code == 200:
                    node.status = NodeStatus.ONLINE
                    node.last_heartbeat = datetime.now(timezone.utc)

                    # 获取节点健康信息
                    health_data = response.json()
                    if health_data.get("metrics"):
                        node.metrics = health_data["metrics"]

                    # 如果节点在线但未连接到主节点，重新发送连接请求
                    if not health_data.get("is_connected", True):
                        try:
                            # 获取节点信息以获取机器码
                            info_response = await client.get(f"{node_url}/node/info")
                            if info_response.status_code == 200:
                                node_info = info_response.json()
                                machine_code = node_info.get("machine_code")

                                if machine_code:
                                    from src.core.config import settings
                                    master_url = f"http://{settings.HOST}:{settings.PORT}"

                                    # 发送 v2 连接请求
                                    await client.post(
                                        f"{node_url}/node/connect/v2",
                                        json={
                                            "machine_code": machine_code,
                                            "api_key": node.api_key,
                                            "master_url": master_url,
                                            "node_id": node.public_id,
                                            "secret_key": node.secret_key,
                                            "use_websocket": False,
                                        }
                                    )
                                    logger.info(f"已重新连接节点: {node.name}")
                        except Exception as e:
                            logger.warning(f"重新连接节点失败: {e}")
                else:
                    node.status = NodeStatus.OFFLINE
        except Exception as e:
            node.status = NodeStatus.OFFLINE
            logger.debug(f"刷新节点状态失败: {e}")

        await node.save()
        return node

    async def smart_health_check(self) -> Dict[str, Any]:
        """
        智能心跳检测（使用缓存和自适应间隔）
        - 在线节点每3秒检测
        - 离线节点逐渐延长间隔（最长60秒）
        - 失败5次后停止自动检测
        - 手动测试成功后恢复自动检测
        """
        import time
        start_time = time.time()

        # 刷新缓存（如果需要）
        await self.refresh_node_cache()

        now = datetime.now()
        results = {
            "total": len(self._node_cache),
            "checked": 0,
            "skipped": 0,
            "online": 0,
            "offline": 0,
            "suspended": 0,
            "elapsed": 0
        }

        # 并发检测所有需要检测的节点
        tasks = []
        nodes_to_check = []

        for node_id, node in self._node_cache.items():
            state = self._node_states[node_id]

            # 跳过已暂停检测的节点
            if state['suspended']:
                results["suspended"] += 1
                continue

            # 检查是否到了检测时间
            if now >= state['next_check']:
                tasks.append(self._check_single_node(node, state))
                nodes_to_check.append(node)
            else:
                results["skipped"] += 1

        # 并发执行检测
        if tasks:
            check_results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(check_results):
                if isinstance(result, Exception):
                    logger.error(f"节点检测异常: {result}")
                    results["offline"] += 1
                else:
                    results["checked"] += 1
                    if result:
                        results["online"] += 1
                    else:
                        results["offline"] += 1

        results["elapsed"] = time.time() - start_time

        # 记录检测摘要
        if results["checked"] > 0:
            logger.debug(
                f"心跳检测: 总计{results['total']}, "
                f"检测{results['checked']}, 跳过{results['skipped']}, "
                f"在线{results['online']}, 离线{results['offline']}, "
                f"暂停{results['suspended']}, 耗时{results['elapsed']:.2f}s"
            )

        return results

    async def _check_single_node(self, node: Node, state: Dict[str, Any]) -> bool:
        """
        检测单个节点
        返回：True=在线, False=离线
        """
        base_url = f"http://{node.host}:{node.port}"
        old_status = node.status
        is_online = False

        try:
            # 使用短超时（2秒）
            async with httpx.AsyncClient(
                timeout=self.HEARTBEAT_TIMEOUT_REQUEST, 
                trust_env=False
            ) as client:
                response = await client.get(f"{base_url}/node/info")

            if response.status_code == 200:
                is_online = True
                node.status = NodeStatus.ONLINE
                node.last_heartbeat = datetime.now()

                # 重置失败计数
                state['failures'] = 0
                state['next_check'] = datetime.now() + timedelta(
                    seconds=self.HEARTBEAT_INTERVAL_ONLINE
                )

                # 更新节点信息（异步，不阻塞）
                try:
                    data = response.json()
                    if data.get("metrics"):
                        node.metrics = data["metrics"]
                    if data.get("version"):
                        node.version = data["version"]

                    system_info = data.get("system", {})
                    if system_info:
                        if system_info.get("os_type"):
                            node.os_type = system_info["os_type"]
                        if system_info.get("os_version"):
                            node.os_version = system_info["os_version"]
                        if system_info.get("python_version"):
                            node.python_version = system_info["python_version"]
                        if system_info.get("machine_arch"):
                            node.machine_arch = system_info["machine_arch"]
                except Exception as e:
                    logger.debug(f"解析节点 {node.name} 信息失败: {e}")

                # 保存到数据库
                await node.save()

                # 状态变化时记录日志
                if old_status != NodeStatus.ONLINE:
                    logger.info(f"节点 {node.name} 恢复在线")
            else:
                is_online = False
                await self._handle_node_offline(node, state, old_status)

        except Exception as e:
            is_online = False
            logger.debug(f"节点 {node.name} 检测失败: {e}")
            await self._handle_node_offline(node, state, old_status)

        return is_online

    async def _handle_node_offline(self, node: Node, state: Dict[str, Any], old_status: str):
        """处理节点离线"""
        node.status = NodeStatus.OFFLINE
        state['failures'] += 1

        # 根据失败次数调整检测间隔
        if state['failures'] >= self.HEARTBEAT_MAX_FAILURES:
            # 暂停自动检测
            state['suspended'] = True
            logger.warning(
                f"节点 {node.name} 连续失败 {state['failures']} 次，"
                f"已暂停自动检测，等待手动测试"
            )
        else:
            # 逐渐延长检测间隔（指数退避）
            interval = min(
                self.HEARTBEAT_INTERVAL_ONLINE * (2 ** state['failures']),
                self.HEARTBEAT_INTERVAL_OFFLINE
            )
            state['next_check'] = datetime.now() + timedelta(seconds=interval)

            logger.debug(
                f"节点 {node.name} 离线（失败{state['failures']}次），"
                f"下次检测间隔: {interval}秒"
            )

        # 状态变化时保存到数据库
        if old_status != NodeStatus.OFFLINE:
            await node.save()
            logger.warning(f"节点 {node.name} 离线")

    async def manual_test_node(self, node_id: int) -> bool:
        """
        手动测试节点连接
        如果成功，恢复自动心跳检测
        """
        # 强制刷新缓存，确保新节点被加入
        self._cache_updated_at = None
        await self.refresh_node_cache()

        if node_id not in self._node_cache:
            # 如果仍然不在缓存中，尝试直接从数据库获取并添加到缓存
            node = await Node.filter(id=node_id).first()
            if not node:
                logger.error(f"节点 {node_id} 不存在")
                return False

            # 添加到缓存
            self._node_cache[node_id] = node
            self._node_states[node_id] = {
                'failures': 0,
                'next_check': datetime.now(),
                'suspended': False
            }

        node = self._node_cache[node_id]
        state = self._node_states[node_id]

        # 执行检测
        is_online = await self._check_single_node(node, state)

        # 如果成功，恢复自动检测
        if is_online:
            state['suspended'] = False
            state['failures'] = 0
            state['next_check'] = datetime.now() + timedelta(
                seconds=self.HEARTBEAT_INTERVAL_ONLINE
            )
            logger.info(f"节点 {node.name} 手动测试成功，已恢复自动心跳检测")

        return is_online

    async def check_all_nodes_health(self) -> Dict[str, Any]:
        """
        检查所有节点健康状态（兼容旧接口）
        新代码请使用 smart_health_check()
        """
        return await self.smart_health_check()

    # ==================== 节点权限管理 ====================

    async def get_user_nodes(self, user_id: int, is_admin: bool = False) -> List[Node]:
        """
        获取用户可访问的节点列表
        - 管理员：返回所有节点
        - 普通用户：只返回被分配权限的节点
        """
        from src.models import UserNodePermission

        if is_admin:
            return await Node.all().order_by('-created_at')

        # 获取用户有权限的节点ID
        permissions = await UserNodePermission.filter(user_id=user_id).all()
        node_ids = [p.node_id for p in permissions]

        if not node_ids:
            return []

        return await Node.filter(id__in=node_ids).order_by('-created_at')

    async def assign_node_to_user(
        self,
        node_id: int,
        user_id: int,
        permission: str = "use",
        assigned_by: int = None,
        note: str = None
    ) -> bool:
        """
        给用户分配节点权限
        注意：管理员不需要分配权限，因为管理员默认拥有全部节点权限
        """
        from src.models import UserNodePermission, User

        # 检查用户是否是管理员
        user = await User.filter(id=user_id).first()
        if user and user.is_admin:
            raise HTTPException(status_code=400, detail="管理员默认拥有全部节点权限，无需分配")

        # 检查节点是否存在
        node = await Node.filter(id=node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="节点不存在")

        # 检查是否已有权限
        existing = await UserNodePermission.filter(
            user_id=user_id, 
            node_id=node_id
        ).first()

        if existing:
            # 更新现有权限
            existing.permission = permission
            existing.assigned_by = assigned_by
            existing.note = note
            await existing.save()
            logger.info(f"更新用户 {user_id} 的节点 {node.name} 权限: {permission}")
        else:
            # 创建新权限
            await UserNodePermission.create(
                user_id=user_id,
                node_id=node_id,
                permission=permission,
                assigned_by=assigned_by,
                note=note
            )
            logger.info(f"分配节点 {node.name} 给用户 {user_id}, 权限: {permission}")

        return True

    async def revoke_node_from_user(self, node_id: int, user_id: int) -> bool:
        """
        撤销用户的节点权限
        """
        from src.models import UserNodePermission

        deleted = await UserNodePermission.filter(
            user_id=user_id,
            node_id=node_id
        ).delete()

        if deleted:
            logger.info(f"撤销用户 {user_id} 的节点 {node_id} 权限")

        return deleted > 0

    async def get_node_users(self, node_id: int) -> List[Dict[str, Any]]:
        """
        获取节点的授权用户列表
        返回 public_id 供前端使用
        注意：不返回管理员用户，因为管理员默认拥有全部节点权限
        """
        from src.models import UserNodePermission, User

        permissions = await UserNodePermission.filter(node_id=node_id).all()

        if not permissions:
            return []

        # 批量获取所有相关用户，避免 N+1 查询
        user_ids = [perm.user_id for perm in permissions]
        users = await User.filter(id__in=user_ids, is_admin=False).all()
        user_map = {u.id: u for u in users}

        result = []
        for perm in permissions:
            user = user_map.get(perm.user_id)
            # 跳过管理员用户（已在查询中过滤）
            if user:
                result.append({
                    "user_id": user.public_id,  # 返回 public_id 供前端使用
                    "username": user.username,
                    "permission": perm.permission,
                    "assigned_at": perm.assigned_at.isoformat() if perm.assigned_at else None,
                    "note": perm.note
                })

        return result

    async def get_user_node_permissions(self, user_id: int) -> List[Dict[str, Any]]:
        """
        获取用户的所有节点权限
        """
        from src.models import UserNodePermission

        permissions = await UserNodePermission.filter(user_id=user_id).all()

        result = []
        for perm in permissions:
            node = await Node.filter(id=perm.node_id).first()
            if node:
                result.append({
                    "node_id": node.id,
                    "node_name": node.name,
                    "node_host": node.host,
                    "node_port": node.port,
                    "node_status": node.status,
                    "permission": perm.permission,
                    "assigned_at": perm.assigned_at.isoformat() if perm.assigned_at else None,
                    "note": perm.note
                })

        return result

    async def check_user_node_permission(
        self,
        user_id: int,
        node_id: int,
        is_admin: bool = False,
        required_permission: str = "use"
    ) -> bool:
        """
        检查用户是否有节点权限
        """
        from src.models import UserNodePermission

        # 管理员有所有权限
        if is_admin:
            return True

        perm = await UserNodePermission.filter(
            user_id=user_id,
            node_id=node_id
        ).first()

        if not perm:
            return False

        # 检查权限级别
        if required_permission == "view":
            return perm.permission in ["view", "use"]
        elif required_permission == "use":
            return perm.permission == "use"

        return False

    async def batch_assign_nodes(
        self,
        user_id: int,
        node_ids: List[int],
        permission: str = "use",
        assigned_by: int = None
    ) -> Dict[str, int]:
        """
        批量分配节点权限给用户（优化版本，使用bulk_create）
        """
        from src.models import UserNodePermission
        from datetime import datetime

        # 获取已存在的权限
        existing_perms = await UserNodePermission.filter(
            user_id=user_id,
            node_id__in=node_ids
        ).values_list('node_id', flat=True)

        existing_node_ids = set(existing_perms)

        # 过滤出需要新建的权限
        new_permissions = []
        for node_id in node_ids:
            if node_id not in existing_node_ids:
                new_permissions.append(
                    UserNodePermission(
                        user_id=user_id,
                        node_id=node_id,
                        permission=permission,
                        assigned_by=assigned_by,
                        assigned_at=datetime.now()
                    )
                )

        # 批量创建
        if new_permissions:
            await UserNodePermission.bulk_create(new_permissions)

        logger.info(f"批量分配节点权限: 用户{user_id}, 新增{len(new_permissions)}个")

        return {
            "success": len(new_permissions),
            "failed": 0,
            "skipped": len(existing_node_ids)
        }

    async def batch_revoke_nodes(self, user_id: int, node_ids: List[int]) -> Dict[str, int]:
        """
        批量撤销用户的节点权限
        """
        from src.models import UserNodePermission

        deleted = await UserNodePermission.filter(
            user_id=user_id,
            node_id__in=node_ids
        ).delete()

        return {"revoked": deleted}

    # ==================== 历史指标 ====================

    async def get_metrics_history(
        self, 
        node_id: int, 
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        """
        获取节点的历史指标数据
        返回指定时间范围内的心跳记录
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        heartbeats = await NodeHeartbeat.filter(
            node_id=node_id,
            timestamp__gte=cutoff_time
        ).order_by('timestamp').all()

        result = []
        for hb in heartbeats:
            metrics = hb.metrics or {}
            result.append({
                "timestamp": hb.timestamp.isoformat(),
                "cpu": metrics.get("cpu", 0),
                "memory": metrics.get("memory", 0),
                "disk": metrics.get("disk", 0),
                "taskCount": metrics.get("taskCount", 0),
                "runningTasks": metrics.get("runningTasks", 0),
                "uptime": metrics.get("uptime", 0)
            })

        return result

    async def get_cluster_metrics_history(self, hours: int = 24) -> Dict[str, Any]:
        """
        获取集群的历史聚合指标
        按时间点聚合所有节点的指标，返回平均值、最大值、最小值
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        # 获取所有节点
        nodes = await Node.all()
        node_ids = [n.id for n in nodes]

        if not node_ids:
            return {
                "timestamps": [],
                "cpu": {"avg": [], "max": [], "min": []},
                "memory": {"avg": [], "max": [], "min": []}
            }

        # 获取所有心跳记录
        heartbeats = await NodeHeartbeat.filter(
            node_id__in=node_ids,
            timestamp__gte=cutoff_time
        ).order_by('timestamp').all()

        # 根据时间范围决定聚合粒度
        if hours <= 24:
            # 24小时内：按小时聚合
            time_format = "%Y-%m-%d %H:00"
        elif hours <= 168:  # 7天
            # 7天内：按4小时聚合
            time_format = None  # 使用自定义逻辑
            interval_hours = 4
        else:  # 30天或更长
            # 30天内：按天聚合
            time_format = "%Y-%m-%d"

        # 按时间聚合
        time_data: Dict[str, Dict[str, List[float]]] = {}

        for hb in heartbeats:
            # 生成时间key
            if time_format:
                time_key = hb.timestamp.strftime(time_format)
            else:
                # 7天数据：按4小时聚合
                hour_bucket = (hb.timestamp.hour // interval_hours) * interval_hours
                time_key = hb.timestamp.strftime(f"%Y-%m-%d {hour_bucket:02d}:00")

            if time_key not in time_data:
                time_data[time_key] = {"cpu": [], "memory": [], "disk": []}

            metrics = hb.metrics or {}
            if metrics.get("cpu") is not None:
                time_data[time_key]["cpu"].append(metrics.get("cpu", 0))
            if metrics.get("memory") is not None:
                time_data[time_key]["memory"].append(metrics.get("memory", 0))
            if metrics.get("disk") is not None:
                time_data[time_key]["disk"].append(metrics.get("disk", 0))

        # 计算每个时间点的平均、最大、最小值
        timestamps = []
        cpu_avg = []
        cpu_max = []
        cpu_min = []
        memory_avg = []
        memory_max = []
        memory_min = []

        for time_key in sorted(time_data.keys()):
            data = time_data[time_key]
            timestamps.append(time_key)

            # CPU
            if data["cpu"]:
                cpu_avg.append(round(sum(data["cpu"]) / len(data["cpu"]), 1))
                cpu_max.append(round(max(data["cpu"]), 1))
                cpu_min.append(round(min(data["cpu"]), 1))
            else:
                cpu_avg.append(0)
                cpu_max.append(0)
                cpu_min.append(0)

            # Memory
            if data["memory"]:
                memory_avg.append(round(sum(data["memory"]) / len(data["memory"]), 1))
                memory_max.append(round(max(data["memory"]), 1))
                memory_min.append(round(min(data["memory"]), 1))
            else:
                memory_avg.append(0)
                memory_max.append(0)
                memory_min.append(0)

        return {
            "timestamps": timestamps,
            "cpu": {
                "avg": cpu_avg,
                "max": cpu_max,
                "min": cpu_min
            },
            "memory": {
                "avg": memory_avg,
                "max": memory_max,
                "min": memory_min
            }
        }


    async def get_node_by_public_id(self, public_id: str) -> Optional[Node]:
        """根据 public_id 获取节点
        
        Args:
            public_id: 节点的公开 ID
            
        Returns:
            节点对象，如果不存在则返回 None
        """
        return await Node.filter(public_id=public_id).first()

    async def verify_api_key(self, node: Node, api_key: str) -> bool:
        """验证节点的 API Key
        
        Args:
            node: 节点对象
            api_key: 要验证的 API Key
            
        Returns:
            验证是否通过
        """
        if not node or not api_key:
            return False
        return node.api_key == api_key


# 创建服务实例
node_service = NodeService()
