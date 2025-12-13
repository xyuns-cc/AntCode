"""
NodeService gRPC 服务实现

实现 NodeService 的所有 RPC 方法。
"""
import asyncio
from datetime import datetime
from typing import AsyncIterator, Dict, Optional, Any

import grpc
from grpc import aio as grpc_aio
from loguru import logger

from src.grpc_generated import (
    NodeServiceServicer,
    NodeMessage,
    MasterMessage,
    RegisterRequest,
    RegisterResponse,
    Ping,
    Timestamp,
)
from src.services.grpc.dispatcher import MessageDispatcher, NodeContext, message_dispatcher
from src.services.grpc.config import grpc_config


class NodeConnection:
    """节点连接管理"""
    
    def __init__(
        self,
        node_id: str,
        context: NodeContext,
        send_queue: asyncio.Queue,
    ):
        self.node_id = node_id
        self.context = context
        self.send_queue = send_queue
        self.connected_at = datetime.now()
        self._closed = False
    
    @property
    def is_closed(self) -> bool:
        return self._closed
    
    def close(self) -> None:
        self._closed = True
    
    async def send(self, message: MasterMessage) -> bool:
        """发送消息到节点
        
        Args:
            message: 要发送的消息
            
        Returns:
            是否成功加入发送队列
        """
        if self._closed:
            return False
        
        try:
            await self.send_queue.put(message)
            return True
        except Exception as e:
            logger.error(f"发送消息失败 - 节点: {self.node_id}, 错误: {e}")
            return False


class NodeServiceImpl(NodeServiceServicer):
    """NodeService gRPC 服务实现
    
    处理节点注册和双向流通信。
    """
    
    # 认证 metadata 键名
    AUTH_NODE_ID_KEY = "x-node-id"
    AUTH_API_KEY_KEY = "x-api-key"
    
    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        heartbeat_interval: int = None,
    ):
        """初始化服务
        
        Args:
            dispatcher: 消息分发器，默认使用全局实例
            heartbeat_interval: 心跳间隔（秒）
        """
        self._dispatcher = dispatcher or message_dispatcher
        self._heartbeat_interval = heartbeat_interval or grpc_config.heartbeat_interval
        self._connections: Dict[str, NodeConnection] = {}
        self._lock = asyncio.Lock()
    
    @property
    def connected_nodes(self) -> list[str]:
        """获取已连接的节点 ID 列表"""
        return list(self._connections.keys())
    
    @property
    def connection_count(self) -> int:
        """获取当前连接数"""
        return len(self._connections)
    
    def _extract_auth_metadata(
        self,
        context: grpc_aio.ServicerContext
    ) -> tuple[Optional[str], Optional[str]]:
        """从 gRPC context 中提取认证信息
        
        Args:
            context: gRPC 服务上下文
            
        Returns:
            (node_id, api_key) 元组
        """
        metadata = dict(context.invocation_metadata())
        node_id = metadata.get(self.AUTH_NODE_ID_KEY)
        api_key = metadata.get(self.AUTH_API_KEY_KEY)
        return node_id, api_key
    
    async def _authenticate(
        self,
        node_id: str,
        api_key: str,
        context: grpc_aio.ServicerContext
    ) -> bool:
        """验证节点认证信息
        
        Args:
            node_id: 节点 ID
            api_key: API 密钥
            context: gRPC 服务上下文
            
        Returns:
            认证是否成功
        """
        if not node_id or not api_key:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "缺少认证信息: 需要 x-node-id 和 x-api-key"
            )
            return False
        
        # TODO: 实际的认证逻辑，验证 node_id 和 api_key
        # 这里需要调用 node_service 验证节点
        try:
            from src.services.nodes import node_service
            node = await node_service.get_node_by_public_id(node_id)
            
            if node is None:
                logger.warning(f"节点不存在: {node_id}")
                await context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    f"节点不存在: {node_id}"
                )
                return False
            
            # 验证 API Key
            if not await node_service.verify_api_key(node, api_key):
                logger.warning(f"API Key 验证失败: {node_id}")
                await context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    "API Key 验证失败"
                )
                return False
            
            return True
            
        except ImportError:
            # 如果 node_service 不可用，使用简单验证
            logger.warning("node_service 不可用，使用简单认证")
            return bool(node_id and api_key)
        except Exception as e:
            logger.error(f"认证过程出错: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"认证过程出错: {e}"
            )
            return False
    
    async def _add_connection(self, connection: NodeConnection) -> None:
        """添加节点连接"""
        async with self._lock:
            # 如果已存在旧连接，先关闭
            old_conn = self._connections.get(connection.node_id)
            if old_conn is not None:
                logger.info(f"关闭旧连接: {connection.node_id}")
                old_conn.close()
            
            self._connections[connection.node_id] = connection
            logger.info(f"节点已连接: {connection.node_id}, 当前连接数: {len(self._connections)}")
    
    async def _remove_connection(self, node_id: str) -> None:
        """移除节点连接"""
        async with self._lock:
            if node_id in self._connections:
                self._connections[node_id].close()
                del self._connections[node_id]
                logger.info(f"节点已断开: {node_id}, 当前连接数: {len(self._connections)}")
    
    async def get_connection(self, node_id: str) -> Optional[NodeConnection]:
        """获取节点连接"""
        return self._connections.get(node_id)
    
    async def send_to_node(self, node_id: str, message: MasterMessage) -> bool:
        """向指定节点发送消息
        
        Args:
            node_id: 节点 ID
            message: 要发送的消息
            
        Returns:
            是否成功发送
        """
        connection = await self.get_connection(node_id)
        if connection is None:
            logger.warning(f"节点未连接: {node_id}")
            return False
        
        return await connection.send(message)
    
    async def broadcast(self, message: MasterMessage, exclude: list[str] = None) -> int:
        """广播消息到所有连接的节点
        
        Args:
            message: 要广播的消息
            exclude: 要排除的节点 ID 列表
            
        Returns:
            成功发送的节点数
        """
        exclude = exclude or []
        success_count = 0
        
        for node_id, connection in list(self._connections.items()):
            if node_id in exclude:
                continue
            
            if await connection.send(message):
                success_count += 1
        
        return success_count
    
    async def Register(
        self,
        request: RegisterRequest,
        context: grpc_aio.ServicerContext
    ) -> RegisterResponse:
        """处理节点注册请求
        
        Args:
            request: 注册请求
            context: gRPC 服务上下文
            
        Returns:
            注册响应
        """
        logger.info(f"收到注册请求 - node_id: {request.node_id}, machine_code: {request.machine_code}")
        
        # 验证认证信息
        if not request.api_key:
            return RegisterResponse(
                success=False,
                error="缺少 API Key"
            )
        
        try:
            # TODO: 实际的注册逻辑
            # 这里需要调用 node_service 进行注册或验证
            from src.services.nodes import node_service
            
            # 验证节点
            node = await node_service.get_node_by_public_id(request.node_id)
            if node is None:
                return RegisterResponse(
                    success=False,
                    error=f"节点不存在: {request.node_id}"
                )
            
            # 验证 API Key
            if not await node_service.verify_api_key(node, request.api_key):
                return RegisterResponse(
                    success=False,
                    error="API Key 验证失败"
                )
            
            logger.info(f"节点注册成功: {request.node_id}")
            return RegisterResponse(
                success=True,
                node_id=request.node_id,
                heartbeat_interval=self._heartbeat_interval,
            )
            
        except ImportError:
            # 如果 node_service 不可用，返回简单成功
            logger.warning("node_service 不可用，使用简单注册")
            return RegisterResponse(
                success=True,
                node_id=request.node_id,
                heartbeat_interval=self._heartbeat_interval,
            )
        except Exception as e:
            logger.error(f"注册过程出错: {e}")
            return RegisterResponse(
                success=False,
                error=f"注册失败: {e}"
            )
    
    async def NodeStream(
        self,
        request_iterator: AsyncIterator[NodeMessage],
        context: grpc_aio.ServicerContext
    ) -> AsyncIterator[MasterMessage]:
        """处理双向流通信
        
        Args:
            request_iterator: 节点消息流
            context: gRPC 服务上下文
            
        Yields:
            主控消息
        """
        # 提取认证信息
        node_id, api_key = self._extract_auth_metadata(context)
        
        # 验证认证
        if not await self._authenticate(node_id, api_key, context):
            return
        
        # 创建节点上下文
        node_context = NodeContext(
            node_id=node_id,
            api_key=api_key,
        )
        
        # 创建发送队列
        send_queue: asyncio.Queue[MasterMessage] = asyncio.Queue()
        
        # 创建连接
        connection = NodeConnection(
            node_id=node_id,
            context=node_context,
            send_queue=send_queue,
        )
        
        # 添加连接
        await self._add_connection(connection)
        
        try:
            # 创建接收任务
            receive_task = asyncio.create_task(
                self._handle_incoming_messages(request_iterator, node_context, context)
            )
            
            # 发送消息循环
            while not connection.is_closed:
                try:
                    # 等待发送队列中的消息，带超时
                    message = await asyncio.wait_for(
                        send_queue.get(),
                        timeout=1.0
                    )
                    yield message
                    
                except asyncio.TimeoutError:
                    # 检查连接状态
                    if context.cancelled():
                        break
                    continue
                except asyncio.CancelledError:
                    break
            
            # 取消接收任务
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
                
        except Exception as e:
            logger.error(f"NodeStream 错误 - 节点: {node_id}, 错误: {e}")
        finally:
            # 移除连接
            await self._remove_connection(node_id)
    
    async def _handle_incoming_messages(
        self,
        request_iterator: AsyncIterator[NodeMessage],
        node_context: NodeContext,
        grpc_context: grpc_aio.ServicerContext
    ) -> None:
        """处理接收到的消息
        
        Args:
            request_iterator: 消息迭代器
            node_context: 节点上下文
            grpc_context: gRPC 上下文
        """
        try:
            async for message in request_iterator:
                if grpc_context.cancelled():
                    break
                
                try:
                    # 分发消息到处理器
                    await self._dispatcher.dispatch(message, node_context)
                except Exception as e:
                    logger.error(
                        f"消息处理失败 - 节点: {node_context.node_id}, "
                        f"消息类型: {message.WhichOneof('payload')}, 错误: {e}"
                    )
                    
        except grpc_aio.AioRpcError as e:
            if e.code() != grpc.StatusCode.CANCELLED:
                from src.services.grpc.metrics import log_grpc_error, get_grpc_metrics_collector
                metrics = get_grpc_metrics_collector()
                log_grpc_error(
                    error_message=str(e.details()),
                    error_code=str(e.code()),
                    node_id=node_context.node_id,
                    operation="receive_message",
                    active_connections=metrics.active_connections,
                    total_messages_sent=metrics.messages_sent,
                    total_messages_received=metrics.messages_received,
                )
                metrics.record_error(
                    error_message=str(e.details()),
                    error_code=str(e.code()),
                    node_id=node_context.node_id,
                    operation="receive_message",
                )
        except Exception as e:
            from src.services.grpc.metrics import log_grpc_error, get_grpc_metrics_collector
            metrics = get_grpc_metrics_collector()
            log_grpc_error(
                error_message=str(e),
                node_id=node_context.node_id,
                operation="receive_message",
                active_connections=metrics.active_connections,
            )
            metrics.record_error(
                error_message=str(e),
                node_id=node_context.node_id,
                operation="receive_message",
            )


# 全局服务实例
node_service_impl: Optional[NodeServiceImpl] = None


def get_node_service_impl() -> NodeServiceImpl:
    """获取全局 NodeService 实现实例"""
    global node_service_impl
    if node_service_impl is None:
        node_service_impl = NodeServiceImpl()
    return node_service_impl
