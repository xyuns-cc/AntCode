"""
通信管理器 - 协议选择和故障转移

提供协议选择、自动故障转移和升级功能：
- 支持 gRPC 和 HTTP 协议
- gRPC 优先，失败时自动降级到 HTTP
- 定期尝试从 HTTP 升级到 gRPC

Requirements: 8.2, 8.3, 8.4, 8.5
"""

import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable, List, Dict, Any

from loguru import logger

from ..domain.models import (
    ConnectionConfig,
    ConnectionState,
    Protocol,
    Heartbeat,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskCancel,
    GrpcMetrics,
)
from ..domain.events import (
    event_bus,
    ConnectionStateChanged,
    ProtocolFallback,
    ProtocolUpgrade,
)
from .protocol import TransportProtocol, ConnectionError as TransportConnectionError
from .grpc_client import GrpcClient
from .http_client import HttpClient
from .resilient_client import ResilientGrpcClient


# 升级检查间隔（秒）
DEFAULT_UPGRADE_CHECK_INTERVAL = 60.0


class CommunicationManager(TransportProtocol):
    """
    通信管理器
    
    管理 gRPC 和 HTTP 协议的选择和切换。
    
    特性:
    - 协议偏好配置（默认优先 gRPC）
    - gRPC 失败时自动降级到 HTTP
    - 定期尝试从 HTTP 升级到 gRPC
    - 统一的消息发送接口
    
    Requirements: 8.2, 8.3, 8.4, 8.5
    """

    def __init__(
        self,
        upgrade_check_interval: float = DEFAULT_UPGRADE_CHECK_INTERVAL,
    ):
        """
        初始化通信管理器
        
        Args:
            upgrade_check_interval: 升级检查间隔（秒）
        """
        # 传输客户端
        self._grpc_client: Optional[GrpcClient] = None
        self._http_client: Optional[HttpClient] = None
        self._resilient_grpc: Optional[ResilientGrpcClient] = None
        
        # 当前活跃的传输
        self._active_transport: Optional[TransportProtocol] = None
        self._current_protocol = Protocol.NONE
        self._preferred_protocol = Protocol.GRPC
        
        # 配置
        self._config: Optional[ConnectionConfig] = None
        self._upgrade_check_interval = upgrade_check_interval
        
        # 状态
        self._state = ConnectionState.DISCONNECTED
        self._running = False
        self._upgrade_task: Optional[asyncio.Task] = None
        
        # 回调函数
        self._on_task_dispatch: Optional[Callable[[TaskDispatch], Awaitable[None]]] = None
        self._on_task_cancel: Optional[Callable[[TaskCancel], Awaitable[None]]] = None
        
        # 指标
        self._fallback_count = 0
        self._upgrade_count = 0
        self._last_fallback_time: Optional[datetime] = None
        self._last_upgrade_time: Optional[datetime] = None

    @property
    def protocol_name(self) -> str:
        return f"manager-{self._current_protocol.value}"

    @property
    def is_connected(self) -> bool:
        return self._active_transport is not None and self._active_transport.is_connected

    @property
    def metrics(self) -> GrpcMetrics:
        if self._active_transport:
            return self._active_transport.metrics
        return GrpcMetrics()

    @property
    def current_protocol(self) -> Protocol:
        """当前使用的协议"""
        return self._current_protocol

    @property
    def preferred_protocol(self) -> Protocol:
        """偏好的协议"""
        return self._preferred_protocol

    @property
    def state(self) -> ConnectionState:
        """连接状态"""
        return self._state

    @property
    def is_degraded(self) -> bool:
        """是否处于降级状态（使用 HTTP 而非 gRPC）"""
        return (
            self._preferred_protocol == Protocol.GRPC
            and self._current_protocol == Protocol.HTTP
        )

    def get_stats(self) -> Dict[str, Any]:
        """获取通信管理器统计信息"""
        base_stats = super().get_stats()
        base_stats.update({
            "current_protocol": self._current_protocol.value,
            "preferred_protocol": self._preferred_protocol.value,
            "is_degraded": self.is_degraded,
            "fallback_count": self._fallback_count,
            "upgrade_count": self._upgrade_count,
            "last_fallback_time": self._last_fallback_time.isoformat() if self._last_fallback_time else None,
            "last_upgrade_time": self._last_upgrade_time.isoformat() if self._last_upgrade_time else None,
        })
        return base_stats

    async def connect(self, config: ConnectionConfig) -> bool:
        """
        建立连接
        
        根据配置的协议偏好尝试连接：
        1. 如果 prefer_grpc=True，先尝试 gRPC
        2. gRPC 失败则降级到 HTTP
        3. 如果 prefer_grpc=False，直接使用 HTTP
        
        Requirements: 8.2, 8.3
        """
        self._config = config
        self._running = True
        self._preferred_protocol = Protocol.GRPC if config.prefer_grpc else Protocol.HTTP
        
        await self._set_state(ConnectionState.CONNECTING)
        
        # 初始化客户端
        self._init_clients()
        
        # 根据偏好选择协议
        if self._preferred_protocol == Protocol.GRPC:
            # 优先尝试 gRPC
            success = await self._try_connect_grpc()
            if success:
                return True
            
            # gRPC 失败，降级到 HTTP
            logger.warning("gRPC 连接失败，降级到 HTTP")
            success = await self._fallback_to_http()
            if success:
                # 启动升级检查任务
                self._start_upgrade_check()
                return True
            
            await self._set_state(ConnectionState.DISCONNECTED)
            return False
        else:
            # 直接使用 HTTP
            success = await self._try_connect_http()
            if success:
                return True
            
            await self._set_state(ConnectionState.DISCONNECTED)
            return False

    def _init_clients(self):
        """初始化传输客户端"""
        # 创建 gRPC 客户端（带弹性包装）
        self._grpc_client = GrpcClient()
        self._resilient_grpc = ResilientGrpcClient(
            transport=self._grpc_client,
            base_delay=self._config.reconnect_base_delay if self._config else 5.0,
            max_delay=self._config.reconnect_max_delay if self._config else 60.0,
        )
        
        # 创建 HTTP 客户端
        self._http_client = HttpClient()
        
        # 注册回调
        self._resilient_grpc.on_task_dispatch(self._handle_task_dispatch)
        self._resilient_grpc.on_task_cancel(self._handle_task_cancel)
        self._http_client.on_task_dispatch(self._handle_task_dispatch)
        self._http_client.on_task_cancel(self._handle_task_cancel)

    async def _try_connect_grpc(self) -> bool:
        """尝试 gRPC 连接"""
        if not self._resilient_grpc or not self._config:
            return False
        
        try:
            logger.info("尝试建立 gRPC 连接...")
            success = await self._resilient_grpc.connect(self._config)
            
            if success:
                self._active_transport = self._resilient_grpc
                self._current_protocol = Protocol.GRPC
                await self._set_state(ConnectionState.CONNECTED)
                logger.info("gRPC 连接成功")
                return True
            
            return False
            
        except Exception as e:
            logger.warning(f"gRPC 连接异常: {e}")
            return False

    async def _try_connect_http(self) -> bool:
        """尝试 HTTP 连接"""
        if not self._http_client or not self._config:
            return False
        
        try:
            logger.info("尝试建立 HTTP 连接...")
            success = await self._http_client.connect(self._config)
            
            if success:
                self._active_transport = self._http_client
                self._current_protocol = Protocol.HTTP
                await self._set_state(ConnectionState.CONNECTED)
                logger.info("HTTP 连接成功")
                return True
            
            return False
            
        except Exception as e:
            logger.warning(f"HTTP 连接异常: {e}")
            return False

    async def _fallback_to_http(self) -> bool:
        """
        降级到 HTTP
        
        当 gRPC 连接失败时，自动切换到 HTTP。
        
        Requirements: 8.4
        """
        # 断开 gRPC（如果已连接）
        if self._resilient_grpc and self._resilient_grpc.is_connected:
            await self._resilient_grpc.disconnect()
        
        # 尝试 HTTP 连接
        success = await self._try_connect_http()
        
        if success:
            self._fallback_count += 1
            self._last_fallback_time = datetime.now()
            await self._set_state(ConnectionState.DEGRADED)
            
            # 发布降级事件
            await event_bus.publish(ProtocolFallback(
                from_protocol=Protocol.GRPC,
                to_protocol=Protocol.HTTP,
                reason="gRPC 连接失败",
            ))
            
            logger.info(f"已降级到 HTTP (第 {self._fallback_count} 次)")
            return True
        
        return False

    async def _try_upgrade_to_grpc(self) -> bool:
        """
        尝试升级到 gRPC
        
        当前使用 HTTP 时，定期尝试升级到 gRPC。
        
        Requirements: 8.5
        """
        if self._current_protocol != Protocol.HTTP:
            return False
        
        if not self._resilient_grpc or not self._config:
            return False
        
        try:
            logger.debug("尝试升级到 gRPC...")
            
            # 尝试 gRPC 连接
            success = await self._resilient_grpc.connect(self._config)
            
            if success:
                # 断开 HTTP
                if self._http_client:
                    await self._http_client.disconnect()
                
                # 切换到 gRPC
                self._active_transport = self._resilient_grpc
                self._current_protocol = Protocol.GRPC
                self._upgrade_count += 1
                self._last_upgrade_time = datetime.now()
                
                await self._set_state(ConnectionState.CONNECTED)
                
                # 发布升级事件
                await event_bus.publish(ProtocolUpgrade(
                    from_protocol=Protocol.HTTP,
                    to_protocol=Protocol.GRPC,
                ))
                
                logger.info(f"已升级到 gRPC (第 {self._upgrade_count} 次)")
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"升级到 gRPC 失败: {e}")
            return False

    def _start_upgrade_check(self):
        """启动升级检查任务"""
        if self._upgrade_task is None or self._upgrade_task.done():
            self._upgrade_task = asyncio.create_task(self._upgrade_check_loop())

    def _stop_upgrade_check(self):
        """停止升级检查任务"""
        if self._upgrade_task and not self._upgrade_task.done():
            self._upgrade_task.cancel()

    async def _upgrade_check_loop(self):
        """
        升级检查循环
        
        定期尝试从 HTTP 升级到 gRPC。
        
        Requirements: 8.5
        """
        while self._running and self._current_protocol == Protocol.HTTP:
            try:
                await asyncio.sleep(self._upgrade_check_interval)
                
                if not self._running:
                    break
                
                if self._current_protocol == Protocol.HTTP:
                    await self._try_upgrade_to_grpc()
                else:
                    # 已经是 gRPC，停止检查
                    break
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"升级检查异常: {e}")
                await asyncio.sleep(self._upgrade_check_interval)

    async def disconnect(self):
        """
        断开连接
        
        优雅关闭流程：
        1. 刷新待处理的日志
        2. 关闭 gRPC 流
        3. 断开通道
        
        Requirements: 7.4
        """
        self._running = False
        
        # 停止升级检查
        self._stop_upgrade_check()
        
        # 刷新待处理的消息（如果使用弹性客户端）
        if self._resilient_grpc:
            # 获取并发送所有缓冲的消息
            pending_count = getattr(self._resilient_grpc, 'buffered_message_count', 0)
            if pending_count > 0:
                logger.info(f"正在刷新 {pending_count} 条待处理消息...")
        
        # 断开所有客户端（这会关闭 gRPC 流和通道）
        if self._resilient_grpc:
            await self._resilient_grpc.disconnect()
        
        if self._http_client:
            await self._http_client.disconnect()
        
        self._active_transport = None
        self._current_protocol = Protocol.NONE
        await self._set_state(ConnectionState.DISCONNECTED)
        
        logger.info("通信管理器已断开连接")

    async def send_heartbeat(self, heartbeat: Heartbeat) -> bool:
        """发送心跳消息"""
        if not self._active_transport:
            return False
        
        success = await self._active_transport.send_heartbeat(heartbeat)
        
        # 如果 gRPC 发送失败，尝试降级
        if not success and self._current_protocol == Protocol.GRPC:
            await self._handle_send_failure()
        
        return success

    async def send_logs(self, logs: List[LogEntry]) -> bool:
        """发送日志批次"""
        if not self._active_transport:
            return False
        
        success = await self._active_transport.send_logs(logs)
        
        if not success and self._current_protocol == Protocol.GRPC:
            await self._handle_send_failure()
        
        return success

    async def send_task_status(self, status: TaskStatus) -> bool:
        """发送任务状态更新"""
        if not self._active_transport:
            return False
        
        success = await self._active_transport.send_task_status(status)
        
        if not success and self._current_protocol == Protocol.GRPC:
            await self._handle_send_failure()
        
        return success

    async def send_task_ack(self, task_id: str, accepted: bool, reason: Optional[str] = None) -> bool:
        """发送任务确认"""
        if not self._active_transport:
            return False
        return await self._active_transport.send_task_ack(task_id, accepted, reason)

    async def send_cancel_ack(self, task_id: str, success: bool, reason: Optional[str] = None) -> bool:
        """发送取消确认"""
        if not self._active_transport:
            return False
        return await self._active_transport.send_cancel_ack(task_id, success, reason)

    def on_task_dispatch(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """注册任务分发回调"""
        self._on_task_dispatch = callback

    def on_task_cancel(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """注册任务取消回调"""
        self._on_task_cancel = callback

    async def _handle_task_dispatch(self, task: TaskDispatch):
        """处理任务分发"""
        if self._on_task_dispatch:
            await self._on_task_dispatch(task)

    async def _handle_task_cancel(self, cancel: TaskCancel):
        """处理任务取消"""
        if self._on_task_cancel:
            await self._on_task_cancel(cancel)

    async def _handle_send_failure(self):
        """
        处理发送失败
        
        检测 gRPC 连接失败并自动降级到 HTTP。
        
        Requirements: 8.4
        """
        # 检查 gRPC 连接状态
        if self._resilient_grpc and not self._resilient_grpc.is_connected:
            # gRPC 已断开，尝试降级
            logger.warning("gRPC 连接已断开，尝试降级到 HTTP")
            success = await self._fallback_to_http()
            if success:
                self._start_upgrade_check()
            else:
                logger.error("降级到 HTTP 也失败，连接不可用")
                await self._set_state(ConnectionState.DISCONNECTED)

    async def _set_state(self, new_state: ConnectionState):
        """设置连接状态"""
        old_state = self._state
        self._state = new_state
        
        if old_state != new_state:
            await event_bus.publish(ConnectionStateChanged(
                old_state=old_state,
                new_state=new_state,
            ))

    async def force_protocol(self, protocol: Protocol) -> bool:
        """
        强制切换到指定协议
        
        Args:
            protocol: 目标协议
            
        Returns:
            是否切换成功
        """
        if protocol == self._current_protocol:
            return True
        
        if protocol == Protocol.GRPC:
            return await self._try_upgrade_to_grpc()
        elif protocol == Protocol.HTTP:
            return await self._fallback_to_http()
        
        return False
