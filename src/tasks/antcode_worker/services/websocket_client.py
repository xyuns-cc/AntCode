"""
WebSocket 通信客户端

.. deprecated:: 2.0.0
    WebSocket 客户端已被弃用，请使用 gRPC 客户端替代。
    
    迁移指南:
    1. 使用 `transport.CommunicationManager` 替代直接使用 WebSocket 客户端
    2. 配置 `prefer_grpc=True` 启用 gRPC 通信
    3. gRPC 提供更好的性能、类型安全和双向流支持
    
    示例:
        # 旧方式 (已弃用)
        from services.websocket_client import node_ws_client
        await node_ws_client.connect(...)
        
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
    
    详细配置请参考: docs/grpc-communication.md

特性 (已弃用):
- 实时双向通信
- 自动重连与指数退避
- 日志批量发送
- 内置心跳保活
"""
import asyncio
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Callable, List

import ujson
import websockets
from loguru import logger


# 发出弃用警告
warnings.warn(
    "websocket_client 模块已弃用，请使用 transport.CommunicationManager 和 gRPC 客户端替代。"
    "详细迁移指南请参考 docs/grpc-communication.md",
    DeprecationWarning,
    stacklevel=2
)


class MessageType(str, Enum):
    """消息类型枚举
    
    .. deprecated:: 2.0.0
        请使用 gRPC Protocol Buffers 定义的消息类型。
    """
    HEARTBEAT = "heartbeat"
    LOG = "log"
    LOG_BATCH = "log_batch"
    TASK_STATUS = "task_status"
    METRICS = "metrics"
    TASK_DISPATCH = "task_dispatch"
    TASK_CANCEL = "task_cancel"
    CONFIG_UPDATE = "config_update"
    PING = "ping"
    PONG = "pong"


@dataclass
class WebSocketMetrics:
    """WebSocket 指标
    
    .. deprecated:: 2.0.0
        请使用 domain.models.GrpcMetrics 替代。
    """
    connected_at: Optional[datetime] = None
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    reconnect_count: int = 0
    last_ping_latency_ms: float = 0


class NodeWebSocketClient:
    """WebSocket 客户端
    
    .. deprecated:: 2.0.0
        此客户端已被弃用，请使用 gRPC 客户端替代。
        
        迁移步骤:
        1. 使用 `transport.CommunicationManager` 管理通信
        2. 配置 `prefer_grpc=True` 启用 gRPC
        3. gRPC 客户端位于 `transport.grpc_client`
        
        详细迁移指南请参考: docs/grpc-communication.md
    
    注意: 此客户端现在由 CommunicationManager 统一管理
    不建议直接使用，请使用 communication_manager
    """

    HEARTBEAT_INTERVAL = 30
    RECONNECT_DELAY = 5
    MAX_RECONNECT_DELAY = 60
    PING_TIMEOUT = 10
    MESSAGE_QUEUE_SIZE = 1000
    LOG_BATCH_SIZE = 50
    LOG_FLUSH_INTERVAL = 1.0
    CONNECT_TIMEOUT = 10  # 连接超时

    def __init__(self):
        self.ws_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.node_id: Optional[str] = None
        self.machine_code: Optional[str] = None

        self._ws: Optional[WebSocketClientProtocol] = None
        self._connected = False
        self._running = False

        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._log_flush_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MESSAGE_QUEUE_SIZE)
        self._send_task: Optional[asyncio.Task] = None

        self._log_buffer: List[Dict] = []
        self._log_lock = asyncio.Lock()
        self._metrics = WebSocketMetrics()
        self._callbacks: Dict[str, Callable] = {}
        self._reconnect_delay = self.RECONNECT_DELAY

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def metrics(self) -> WebSocketMetrics:
        return self._metrics

    def on(self, event: str, callback: Callable):
        self._callbacks[event] = callback

    async def _emit(self, event: str, data: Any = None):
        if event in self._callbacks:
            try:
                result = self._callbacks[event](data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"回调异常 [{event}]: {e}")

    async def connect(self, master_url: str, machine_code: str, api_key: str, node_id: str):
        # 根据 master_url 协议选择 WebSocket 协议
        # HTTPS -> wss://, HTTP -> ws://（内网 IP 直连场景）
        if master_url.startswith("https://"):
            ws_url = master_url.replace("https://", "wss://")
        elif master_url.startswith("http://"):
            ws_url = master_url.replace("http://", "ws://")
        else:
            ws_url = f"ws://{master_url}"
        self.ws_url = f"{ws_url}/api/v1/nodes/ws/{node_id}"
        self.machine_code = machine_code
        self.api_key = api_key
        self.node_id = node_id

        self._running = True
        await self._do_connect()

    async def _do_connect(self):
        if not self.ws_url:
            return

        try:
            # 使用超时控制连接
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self.ws_url,
                    extra_headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-Node-ID": self.node_id,
                        "X-Machine-Code": self.machine_code,
                    },
                    ping_interval=20,
                    ping_timeout=self.PING_TIMEOUT,
                    close_timeout=5,
                ),
                timeout=self.CONNECT_TIMEOUT
            )

            self._connected = True
            self._metrics.connected_at = datetime.now()
            self._reconnect_delay = self.RECONNECT_DELAY

            logger.info(f"WebSocket 已连接: {self.ws_url}")

            self._receive_task = asyncio.create_task(self._receive_loop())
            self._send_task = asyncio.create_task(self._send_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._log_flush_task = asyncio.create_task(self._log_flush_loop())

            await self.send_heartbeat()
            await self._emit("connect")
        except asyncio.TimeoutError:
            logger.debug(f"WebSocket 连接超时: {self.ws_url}")
            self._connected = False
            await self._emit("error", {"type": "connect", "error": "连接超时"})
            if self._running:
                self._schedule_reconnect()
        except Exception as e:
            logger.debug(f"WebSocket 连接失败: {e}")
            self._connected = False
            await self._emit("error", {"type": "connect", "error": str(e)})
            if self._running:
                self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self):
        while self._running and not self._connected:
            logger.debug(f"将在 {self._reconnect_delay}s 后重连")
            await asyncio.sleep(self._reconnect_delay)

            if not self._running:
                break

            self._reconnect_delay = min(self._reconnect_delay * 1.5, self.MAX_RECONNECT_DELAY)
            self._metrics.reconnect_count += 1
            await self._do_connect()

    async def disconnect(self):
        self._running = False
        await self._flush_logs()

        for task in [self._receive_task, self._send_task, self._heartbeat_task,
                     self._log_flush_task, self._reconnect_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._connected = False
        logger.info("WebSocket 已断开")
        await self._emit("disconnect")

    async def _receive_loop(self):
        try:
            async for message in self._ws:
                try:
                    self._metrics.messages_received += 1
                    self._metrics.bytes_received += len(message)

                    data = ujson.loads(message)
                    await self._handle_message(data.get("type"), data)
                except (ujson.JSONDecodeError, ValueError):
                    logger.warning("无效的 JSON 消息")
                except Exception as e:
                    logger.error(f"消息处理异常: {e}")
        except websockets.ConnectionClosed:
            logger.debug("WebSocket 已关闭")
            self._connected = False
            await self._emit("disconnect")
            if self._running:
                self._schedule_reconnect()
        except Exception as e:
            logger.error(f"接收异常: {e}")
            self._connected = False

    async def _handle_message(self, msg_type: str, data: Dict):
        if msg_type == MessageType.PING:
            await self._send_message({"type": MessageType.PONG, "timestamp": time.time()})
        elif msg_type == MessageType.PONG:
            sent_at = data.get("sent_at", 0)
            if sent_at:
                self._metrics.last_ping_latency_ms = (time.time() - sent_at) * 1000
        elif msg_type == MessageType.TASK_DISPATCH:
            logger.info(f"收到任务: {data.get('task_id')}")
            await self._emit("task_dispatch", data)
        elif msg_type == MessageType.TASK_CANCEL:
            logger.info(f"取消任务: {data.get('task_id')}")
            await self._emit("task_cancel", data)
        elif msg_type == MessageType.CONFIG_UPDATE:
            await self._emit("config_update", data)

    async def _send_loop(self):
        while self._running:
            try:
                message = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                if self._ws and self._connected:
                    await self._ws.send(message)
                    self._metrics.messages_sent += 1
                    self._metrics.bytes_sent += len(message)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"发送异常: {e}")

    async def _send_message(self, data: Dict):
        try:
            await self._message_queue.put(ujson.dumps(data))
        except asyncio.QueueFull:
            logger.warning("消息队列已满")

    async def _heartbeat_loop(self):
        while self._running and self._connected:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                if self._connected:
                    await self.send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳异常: {e}")

    async def send_heartbeat(self):
        from .master_client import master_client

        await self._send_message({
            "type": MessageType.HEARTBEAT,
            "timestamp": time.time(),
            "node_id": self.node_id,
            "status": "online",
            "metrics": master_client.get_system_metrics(),
            "os_info": master_client.get_os_info(),
        })

    async def _log_flush_loop(self):
        while self._running and self._connected:
            try:
                await asyncio.sleep(self.LOG_FLUSH_INTERVAL)
                await self._flush_logs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"日志刷新异常: {e}")

    async def _flush_logs(self):
        async with self._log_lock:
            if not self._log_buffer:
                return
            logs = self._log_buffer[:self.LOG_BATCH_SIZE]
            self._log_buffer = self._log_buffer[self.LOG_BATCH_SIZE:]

        if logs:
            await self._send_message({
                "type": MessageType.LOG_BATCH,
                "timestamp": time.time(),
                "logs": logs,
            })

    async def send_log(self, execution_id: str, log_type: str, content: str):
        async with self._log_lock:
            self._log_buffer.append({
                "execution_id": execution_id,
                "log_type": log_type,
                "content": content,
                "timestamp": time.time(),
            })
            if len(self._log_buffer) >= self.LOG_BATCH_SIZE:
                asyncio.create_task(self._flush_logs())

    async def send_task_status(self, execution_id: str, status: str,
                               exit_code: Optional[int] = None,
                               error_message: Optional[str] = None):
        await self._flush_logs()
        await self._send_message({
            "type": MessageType.TASK_STATUS,
            "timestamp": time.time(),
            "execution_id": execution_id,
            "status": status,
            "exit_code": exit_code,
            "error_message": error_message,
        })

    def get_stats(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "url": self.ws_url,
            "connected_at": self._metrics.connected_at.isoformat() if self._metrics.connected_at else None,
            "messages_sent": self._metrics.messages_sent,
            "messages_received": self._metrics.messages_received,
            "bytes_sent": self._metrics.bytes_sent,
            "bytes_received": self._metrics.bytes_received,
            "reconnect_count": self._metrics.reconnect_count,
            "ping_latency_ms": round(self._metrics.last_ping_latency_ms, 2),
            "log_buffer": len(self._log_buffer),
            "queue_size": self._message_queue.qsize(),
        }


node_ws_client = NodeWebSocketClient()
