"""
统一通讯管理器

.. deprecated:: 2.0.0
    此模块已弃用，请使用 transport.CommunicationManager 替代。
    
    transport.CommunicationManager 提供:
    - gRPC 优先，HTTP 回退（更好的性能和类型安全）
    - 自动协议切换和故障转移
    - 完整的指标监控
    
    迁移指南:
    1. 使用 `from transport import CommunicationManager` 替代
    2. 配置 `prefer_grpc=True` 启用 gRPC 通信
    3. 详细配置请参考: docs/grpc-communication.md
    
    示例:
        # 旧方式 (已弃用)
        from services.communication_manager import communication_manager
        await communication_manager.connect(...)
        
        # 新方式 (推荐)
        from transport import CommunicationManager
        from domain.models import ConnectionConfig
        
        config = ConnectionConfig(
            master_url="http://master:8000",
            node_id="node-001",
            api_key="your-api-key",
            machine_code="ABC123",
            prefer_grpc=True,
        )
        manager = CommunicationManager()
        await manager.connect(config)

特性 (已弃用):
- WebSocket 优先，HTTP 回退
- 自动重连与协议切换
- 连接状态监控
- 高效批量日志发送（使用 LogBuffer）
- gzip 压缩批量发送
- 发送失败时保留日志重试
- 任务完成时立即刷新
- 线程安全
"""
import asyncio
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Callable, List

from loguru import logger

from .log_buffer import LogBuffer


# 发出弃用警告
warnings.warn(
    "services.communication_manager 模块已弃用，请使用 transport.CommunicationManager 替代。"
    "详细迁移指南请参考 docs/grpc-communication.md",
    DeprecationWarning,
    stacklevel=2
)


class ConnectionProtocol(str, Enum):
    """连接协议"""
    NONE = "none"
    HTTP = "http"
    WEBSOCKET = "websocket"


class ConnectionState(str, Enum):
    """连接状态"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"  # WebSocket 失败，使用 HTTP


@dataclass
class ConnectionMetrics:
    """连接指标"""
    protocol: ConnectionProtocol = ConnectionProtocol.NONE
    state: ConnectionState = ConnectionState.DISCONNECTED
    connected_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None

    # WebSocket 指标
    ws_connect_attempts: int = 0
    ws_connect_failures: int = 0
    ws_last_failure: Optional[datetime] = None
    ws_last_failure_reason: str = ""

    # 消息统计
    messages_sent: int = 0
    messages_failed: int = 0
    logs_sent: int = 0
    status_updates_sent: int = 0

    # 延迟统计
    avg_latency_ms: float = 0
    _latency_samples: List[float] = field(default_factory=list)

    def record_latency(self, latency_ms: float):
        """记录延迟"""
        self._latency_samples.append(latency_ms)
        # 保留最近 100 个样本
        if len(self._latency_samples) > 100:
            self._latency_samples = self._latency_samples[-100:]
        self.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)


class CommunicationManager:
    """
    统一通讯管理器
    
    .. deprecated:: 2.0.0
        此类已弃用，请使用 transport.CommunicationManager 替代。
        新的 CommunicationManager 支持 gRPC 优先通信，提供更好的性能和类型安全。
        详细迁移指南请参考: docs/grpc-communication.md
    
    策略 (已弃用):
    1. 首次连接时尝试 WebSocket
    2. WebSocket 失败则回退到 HTTP
    3. 定期尝试恢复 WebSocket 连接
    4. 所有上报操作自动选择可用协议
    """

    # WebSocket 重试配置
    WS_RETRY_INTERVAL = 60  # WebSocket 重试间隔（秒）
    WS_MAX_CONSECUTIVE_FAILURES = 3  # 连续失败次数后暂停重试
    WS_BACKOFF_MULTIPLIER = 2  # 退避倍数
    WS_MAX_BACKOFF = 300  # 最大退避时间（秒）

    # 日志批量配置
    LOG_BATCH_SIZE = 50
    LOG_FLUSH_INTERVAL = 1.0  # 秒
    LOG_BUFFER_MAX_SIZE = 2000

    def __init__(self):
        self._master_url: Optional[str] = None
        self._api_key: Optional[str] = None
        self._secret_key: Optional[str] = None
        self._node_id: Optional[str] = None
        self._machine_code: Optional[str] = None

        self._metrics = ConnectionMetrics()
        self._state_lock = asyncio.Lock()

        # 使用新的 LogBuffer 替代简单的 deque
        self._log_buffer = LogBuffer(
            max_size=self.LOG_BATCH_SIZE,
            flush_interval=self.LOG_FLUSH_INTERVAL,
            max_buffer_lines=self.LOG_BUFFER_MAX_SIZE,
            compress=True,
            send_func=self._send_logs_batch_from_buffer,
        )

        # 保留旧的 _log_lock 用于兼容性
        self._log_lock = asyncio.Lock()

        # 后台任务
        self._ws_retry_task: Optional[asyncio.Task] = None
        self._log_flush_task: Optional[asyncio.Task] = None
        self._running = False

        # 回调
        self._on_protocol_change: Optional[Callable] = None

    @property
    def is_connected(self) -> bool:
        """是否已连接（任一协议）"""
        return self._metrics.state in (ConnectionState.CONNECTED, ConnectionState.DEGRADED)

    @property
    def current_protocol(self) -> ConnectionProtocol:
        """当前使用的协议"""
        return self._metrics.protocol

    @property
    def metrics(self) -> ConnectionMetrics:
        """获取连接指标"""
        return self._metrics

    def set_protocol_change_callback(self, callback: Callable):
        """设置协议切换回调"""
        self._on_protocol_change = callback

    async def connect(
        self,
        master_url: str,
        machine_code: str,
        api_key: str,
        secret_key: str = None,
        node_id: str = None,
        prefer_websocket: bool = True,
    ) -> bool:
        """
        建立连接
        
        Args:
            master_url: 主控地址
            machine_code: 机器码
            api_key: API 密钥
            secret_key: 签名密钥
            node_id: 节点 ID
            prefer_websocket: 是否优先使用 WebSocket
        
        Returns:
            是否连接成功
        """
        async with self._state_lock:
            self._master_url = master_url.rstrip("/")
            self._machine_code = machine_code
            self._api_key = api_key
            self._secret_key = secret_key
            self._node_id = node_id

            self._metrics.state = ConnectionState.CONNECTING

        self._running = True
        connected = False

        # 1. 优先尝试 WebSocket
        if prefer_websocket and node_id:
            connected = await self._try_websocket_connect()

        # 2. WebSocket 失败则使用 HTTP
        if not connected:
            connected = await self._try_http_connect()
            if connected:
                async with self._state_lock:
                    self._metrics.state = ConnectionState.DEGRADED
                    self._metrics.protocol = ConnectionProtocol.HTTP
                logger.info("使用 HTTP 协议连接（WebSocket 不可用）")

        if connected:
            # 启动后台任务
            await self._start_background_tasks()
            self._metrics.connected_at = datetime.now()
            self._metrics.last_activity = datetime.now()
        else:
            self._metrics.state = ConnectionState.DISCONNECTED

        return connected

    async def disconnect(self):
        """断开连接"""
        self._running = False

        # 停止后台任务（LogBuffer.stop() 会自动刷新剩余日志）
        await self._stop_background_tasks()

        # 断开 WebSocket
        from .websocket_client import node_ws_client
        if node_ws_client.is_connected:
            await node_ws_client.disconnect()

        # 断开 HTTP
        from .master_client import master_client
        await master_client.disconnect()

        async with self._state_lock:
            self._metrics.state = ConnectionState.DISCONNECTED
            self._metrics.protocol = ConnectionProtocol.NONE

        logger.info("通讯管理器已断开")

    async def _try_websocket_connect(self) -> bool:
        """尝试 WebSocket 连接"""
        from .websocket_client import node_ws_client

        self._metrics.ws_connect_attempts += 1

        try:
            await node_ws_client.connect(
                master_url=self._master_url,
                machine_code=self._machine_code,
                api_key=self._api_key,
                node_id=self._node_id,
            )

            # 等待连接建立
            await asyncio.sleep(0.5)

            if node_ws_client.is_connected:
                async with self._state_lock:
                    self._metrics.state = ConnectionState.CONNECTED
                    self._metrics.protocol = ConnectionProtocol.WEBSOCKET
                    self._metrics.ws_connect_failures = 0  # 重置失败计数

                logger.info(f"WebSocket 连接成功: {self._master_url}")

                if self._on_protocol_change:
                    try:
                        await self._on_protocol_change(ConnectionProtocol.WEBSOCKET)
                    except Exception:
                        pass

                return True
            else:
                raise ConnectionError("WebSocket 连接未建立")

        except Exception as e:
            self._metrics.ws_connect_failures += 1
            self._metrics.ws_last_failure = datetime.now()
            self._metrics.ws_last_failure_reason = str(e)
            logger.debug(f"WebSocket 连接失败: {e}")
            return False

    async def _try_http_connect(self) -> bool:
        """尝试 HTTP 连接"""
        from .master_client import master_client

        try:
            await master_client.connect(
                master_url=self._master_url,
                machine_code=self._machine_code,
                api_key=self._api_key,
                secret_key=self._secret_key,
                node_id=self._node_id,
            )

            if master_client.is_connected:
                logger.info(f"HTTP 连接成功: {self._master_url}")
                return True
            else:
                raise ConnectionError("HTTP 连接未建立")

        except Exception as e:
            logger.warning(f"HTTP 连接失败: {e}")
            return False

    async def _start_background_tasks(self):
        """启动后台任务"""
        # WebSocket 重试任务
        if self._ws_retry_task is None or self._ws_retry_task.done():
            self._ws_retry_task = asyncio.create_task(self._ws_retry_loop())

        # 启动 LogBuffer 后台刷新任务
        await self._log_buffer.start()

    async def _stop_background_tasks(self):
        """停止后台任务"""
        # 停止 WebSocket 重试任务
        if self._ws_retry_task and not self._ws_retry_task.done():
            self._ws_retry_task.cancel()
            try:
                await self._ws_retry_task
            except asyncio.CancelledError:
                pass

        self._ws_retry_task = None

        # 停止 LogBuffer（会自动刷新剩余日志）
        await self._log_buffer.stop()

    async def _ws_retry_loop(self):
        """WebSocket 重连循环"""
        backoff = self.WS_RETRY_INTERVAL

        while self._running:
            try:
                await asyncio.sleep(backoff)

                if not self._running:
                    break

                # 只在 HTTP 模式下尝试恢复 WebSocket
                if self._metrics.protocol != ConnectionProtocol.HTTP:
                    continue

                # 检查是否应该尝试重连
                if self._metrics.ws_connect_failures >= self.WS_MAX_CONSECUTIVE_FAILURES:
                    # 使用指数退避
                    backoff = min(backoff * self.WS_BACKOFF_MULTIPLIER, self.WS_MAX_BACKOFF)
                    logger.debug(f"WebSocket 连续失败 {self._metrics.ws_connect_failures} 次，"
                                f"下次重试间隔: {backoff}s")
                    continue

                logger.debug("尝试恢复 WebSocket 连接...")

                if await self._try_websocket_connect():
                    # 成功恢复，重置退避
                    backoff = self.WS_RETRY_INTERVAL
                    logger.info("WebSocket 连接已恢复")
                else:
                    # 失败，增加退避
                    backoff = min(backoff * self.WS_BACKOFF_MULTIPLIER, self.WS_MAX_BACKOFF)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebSocket 重连循环异常: {e}")
                await asyncio.sleep(5)

    async def _send_logs_batch_from_buffer(self, logs: List[Dict], compressed: bool) -> bool:
        """
        从 LogBuffer 发送日志批次
        
        Args:
            logs: 日志字典列表
            compressed: 是否启用压缩（由 LogBuffer 传入）
            
        Returns:
            是否发送成功
        """
        return await self._send_logs_batch(logs)

    async def _send_logs_batch(self, logs: List[Dict]) -> bool:
        """发送日志批次"""
        from .websocket_client import node_ws_client
        from .master_client import master_client

        start_time = time.time()
        success = False

        # 优先使用 WebSocket
        if self._metrics.protocol == ConnectionProtocol.WEBSOCKET and node_ws_client.is_connected:
            try:
                for log in logs:
                    await node_ws_client.send_log(
                        log["execution_id"],
                        log["log_type"],
                        log["content"]
                    )
                success = True
                self._metrics.logs_sent += len(logs)
            except Exception as e:
                logger.debug(f"WebSocket 发送日志失败: {e}")
                # WebSocket 失败，降级到 HTTP
                await self._degrade_to_http()

        # HTTP 回退
        if not success and master_client.is_connected:
            try:
                for log in logs:
                    await master_client.report_log_line(
                        log["execution_id"],
                        log["log_type"],
                        log["content"]
                    )
                success = True
                self._metrics.logs_sent += len(logs)
            except Exception as e:
                logger.warning(f"HTTP 发送日志失败: {e}")

        if success:
            latency = (time.time() - start_time) * 1000
            self._metrics.record_latency(latency)
            self._metrics.messages_sent += 1
            self._metrics.last_activity = datetime.now()
        else:
            self._metrics.messages_failed += 1

        return success

    async def _degrade_to_http(self):
        """降级到 HTTP"""
        from .websocket_client import node_ws_client

        if self._metrics.protocol == ConnectionProtocol.WEBSOCKET:
            logger.warning("WebSocket 连接异常，降级到 HTTP")

            # 断开 WebSocket
            try:
                await node_ws_client.disconnect()
            except Exception:
                pass

            async with self._state_lock:
                self._metrics.protocol = ConnectionProtocol.HTTP
                self._metrics.state = ConnectionState.DEGRADED
                self._metrics.ws_connect_failures += 1
                self._metrics.ws_last_failure = datetime.now()

            if self._on_protocol_change:
                try:
                    await self._on_protocol_change(ConnectionProtocol.HTTP)
                except Exception:
                    pass

    # ==================== 公共 API ====================

    async def report_log(self, execution_id: str, log_type: str, content: str):
        """
        上报日志行
        
        日志会先进入缓冲区，定期批量发送
        """
        if not self.is_connected:
            return

        await self._log_buffer.add(execution_id, log_type, content)

    async def report_task_status(
        self,
        execution_id: str,
        status: str,
        exit_code: int = None,
        error_message: str = None,
    ) -> bool:
        """
        上报任务状态
        
        状态更新会立即发送（不经过缓冲）
        """
        if not self.is_connected:
            return False

        from .websocket_client import node_ws_client
        from .master_client import master_client

        # 先刷新该任务的日志，确保日志在状态之前到达
        await self._log_buffer.flush_execution(execution_id)

        start_time = time.time()
        success = False

        # 优先使用 WebSocket
        if self._metrics.protocol == ConnectionProtocol.WEBSOCKET and node_ws_client.is_connected:
            try:
                await node_ws_client.send_task_status(
                    execution_id, status, exit_code, error_message
                )
                success = True
            except Exception as e:
                logger.debug(f"WebSocket 发送状态失败: {e}")
                await self._degrade_to_http()

        # HTTP 回退
        if not success and master_client.is_connected:
            try:
                success = await master_client.report_task_status(
                    execution_id, status, exit_code, error_message
                )
            except Exception as e:
                logger.warning(f"HTTP 发送状态失败: {e}")

        if success:
            latency = (time.time() - start_time) * 1000
            self._metrics.record_latency(latency)
            self._metrics.status_updates_sent += 1
            self._metrics.last_activity = datetime.now()

        return success

    async def flush_execution(self, execution_id: str) -> None:
        """
        立即刷新指定执行的所有日志（任务完成时调用）
        
        Args:
            execution_id: 执行 ID
        """
        await self._log_buffer.flush_execution(execution_id)

    def get_stats(self) -> Dict[str, Any]:
        """获取通讯统计"""
        from .websocket_client import node_ws_client
        from .master_client import master_client

        # 获取 LogBuffer 统计
        log_buffer_stats = self._log_buffer.get_stats()

        return {
            "protocol": self._metrics.protocol.value,
            "state": self._metrics.state.value,
            "connected_at": self._metrics.connected_at.isoformat() if self._metrics.connected_at else None,
            "last_activity": self._metrics.last_activity.isoformat() if self._metrics.last_activity else None,
            "messages_sent": self._metrics.messages_sent,
            "messages_failed": self._metrics.messages_failed,
            "logs_sent": self._metrics.logs_sent,
            "status_updates_sent": self._metrics.status_updates_sent,
            "avg_latency_ms": round(self._metrics.avg_latency_ms, 2),
            "log_buffer": log_buffer_stats,
            "websocket": {
                "connected": node_ws_client.is_connected,
                "connect_attempts": self._metrics.ws_connect_attempts,
                "connect_failures": self._metrics.ws_connect_failures,
                "last_failure": self._metrics.ws_last_failure.isoformat() if self._metrics.ws_last_failure else None,
                "last_failure_reason": self._metrics.ws_last_failure_reason,
            },
            "http": {
                "connected": master_client.is_connected,
                "metrics": {
                    "total": master_client.metrics.total_requests,
                    "success": master_client.metrics.success_requests,
                    "failed": master_client.metrics.failed_requests,
                }
            }
        }


# 全局实例
communication_manager = CommunicationManager()
