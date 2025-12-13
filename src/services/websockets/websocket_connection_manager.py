"""
WebSocket连接管理器 - 生产环境优化版本
负责管理WebSocket连接的生命周期、心跳检测和消息广播
"""
import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from enum import Enum

from fastapi import WebSocket
from loguru import logger

from src.utils.serialization import to_json


class ConnectionState(Enum):
    """连接状态枚举"""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass
class ConnectionInfo:
    """连接信息"""
    connection_id: str
    execution_id: str
    user_id: int
    websocket: WebSocket
    state: ConnectionState = ConnectionState.CONNECTING
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_ping: Optional[datetime] = None
    last_pong: Optional[datetime] = None
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    missed_pongs: int = 0


class ConnectionPool:
    """连接池管理 - 高性能版本
    
    优化点：
    1. 使用分段锁减少锁竞争
    2. 读操作无锁
    3. 连接数限制和内存保护
    """

    def __init__(
        self, 
        max_connections_per_execution: int = 50,
        max_total_connections: int = 10000
    ):
        from src.core.config import settings
        self.max_connections_per_execution = getattr(settings, "WEBSOCKET_MAX_CONN_PER_EXECUTION", max_connections_per_execution)
        self.max_total_connections = getattr(settings, "WEBSOCKET_MAX_TOTAL_CONN", max_total_connections)
        self._connections: Dict[str, Dict[str, ConnectionInfo]] = defaultdict(dict)
        # 分段锁：每个 execution_id 一个锁
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._global_lock = asyncio.Lock()  # 仅用于创建新的 execution 分段
        self._total_count = 0

    async def add_connection(self, connection_info: ConnectionInfo) -> bool:
        """添加连接"""
        execution_id = connection_info.execution_id
        connection_id = connection_info.connection_id

        # 检查总连接数限制
        if self._total_count >= self.max_total_connections:
            logger.error(f"总连接数超限: {self._total_count}/{self.max_total_connections}")
            return False

        # 获取或创建分段锁
        async with self._global_lock:
            lock = self._locks[execution_id]

        async with lock:
            # 检查单执行连接数限制
            if len(self._connections[execution_id]) >= self.max_connections_per_execution:
                oldest = min(
                    self._connections[execution_id].values(),
                    key=lambda c: c.connected_at
                )
                await self._close_connection_unsafe(oldest)
                self._total_count -= 1
                logger.warning(f"执行ID {execution_id} 连接数超限，移除最旧连接")

            self._connections[execution_id][connection_id] = connection_info
            connection_info.state = ConnectionState.CONNECTED
            self._total_count += 1
            return True

    async def remove_connection(self, execution_id: str, connection_id: str) -> bool:
        """移除连接"""
        if execution_id not in self._connections:
            return False

        lock = self._locks.get(execution_id)
        if not lock:
            return False

        async with lock:
            if connection_id in self._connections.get(execution_id, {}):
                conn = self._connections[execution_id].pop(connection_id)
                conn.state = ConnectionState.CLOSED
                self._total_count -= 1

                # 清理空的 execution
                if not self._connections[execution_id]:
                    del self._connections[execution_id]
                    # 延迟清理锁，避免竞态
                return True
            return False

    async def _close_connection_unsafe(self, conn: ConnectionInfo):
        """关闭连接（调用者需持有锁）"""
        try:
            conn.state = ConnectionState.CLOSING
            await asyncio.wait_for(
                conn.websocket.close(code=1000, reason="连接被替换"),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"关闭连接超时: {conn.connection_id}")
        except Exception as e:
            logger.debug(f"关闭连接时忽略异常: {e}")
        finally:
            conn.state = ConnectionState.CLOSED

    def get_connection(self, execution_id: str, connection_id: str) -> Optional[ConnectionInfo]:
        """获取单个连接（无锁读取）"""
        return self._connections.get(execution_id, {}).get(connection_id)

    def get_connections(self, execution_id: str) -> List[ConnectionInfo]:
        """获取执行ID的所有连接"""
        return list(self._connections.get(execution_id, {}).values())

    def get_all_connections(self) -> Dict[str, List[ConnectionInfo]]:
        """获取所有连接"""
        return {eid: list(conns.values()) for eid, conns in self._connections.items()}

    def get_connection_count(self, execution_id: str) -> int:
        """获取连接数"""
        return len(self._connections.get(execution_id, {}))

    def get_total_connection_count(self) -> int:
        """获取总连接数"""
        return sum(len(conns) for conns in self._connections.values())

    def update_activity(self, execution_id: str, connection_id: str):
        """更新连接活动时间"""
        conn = self.get_connection(execution_id, connection_id)
        if conn:
            conn.last_activity = datetime.now(timezone.utc)


class HeartbeatManager:
    """心跳管理器"""

    def __init__(
        self,
        ping_interval: float = 30.0,
        pong_timeout: float = 10.0,
        max_missed_pongs: int = 3
    ):
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout
        self.max_missed_pongs = max_missed_pongs
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self, connection_pool: ConnectionPool, on_timeout: callable):
        """启动心跳检测"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(
            self._heartbeat_loop(connection_pool, on_timeout)
        )
        logger.info(f"心跳管理器已启动: 间隔={self.ping_interval}s, 超时={self.pong_timeout}s")

    async def stop(self):
        """停止心跳检测"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("心跳管理器已停止")

    async def _heartbeat_loop(self, pool: ConnectionPool, on_timeout: callable):
        """心跳循环"""
        while self._running:
            try:
                await asyncio.sleep(self.ping_interval)
                await self._send_pings(pool, on_timeout)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳循环异常: {e}")

    async def _send_pings(self, pool: ConnectionPool, on_timeout: callable):
        """发送心跳"""
        now = datetime.now(timezone.utc)
        all_connections = pool.get_all_connections()

        for execution_id, connections in all_connections.items():
            for conn in connections:
                if conn.state != ConnectionState.CONNECTED:
                    continue

                # 检查是否超时
                if conn.last_ping and not conn.last_pong:
                    time_since_ping = (now - conn.last_ping).total_seconds()
                    if time_since_ping > self.pong_timeout:
                        conn.missed_pongs += 1
                        if conn.missed_pongs >= self.max_missed_pongs:
                            logger.warning(f"连接 {conn.connection_id} 心跳超时，准备断开")
                            await on_timeout(conn)
                            continue

                # 发送 ping
                try:
                    ping_message = {
                        "type": "ping",
                        "timestamp": now.isoformat(),
                        "server_time": now.isoformat()
                    }
                    await conn.websocket.send_json(ping_message)
                    conn.last_ping = now
                except Exception as e:
                    logger.debug(f"发送心跳失败: {conn.connection_id}, {e}")
                    await on_timeout(conn)

    def record_pong(self, conn: ConnectionInfo):
        """记录 pong 响应"""
        conn.last_pong = datetime.now(timezone.utc)
        conn.missed_pongs = 0


class MessageQueue:
    """消息队列管理 - 高性能版本
    
    优化点：
    1. 无锁队列操作（deque 是线程安全的）
    2. 批量发送减少系统调用
    3. 背压控制防止内存溢出
    """

    def __init__(
        self, 
        max_queue_size: int = 1000, 
        batch_size: int = 20,
        flush_interval: float = 0.05  # 50ms 刷新间隔
    ):
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queues: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_queue_size))
        self._processing: Dict[str, asyncio.Task] = {}
        self._dropped_count: Dict[str, int] = defaultdict(int)

    async def enqueue(self, execution_id: str, message: dict) -> bool:
        """入队消息（无锁）"""
        queue = self._queues[execution_id]

        # deque 的 maxlen 会自动丢弃旧消息
        was_full = len(queue) >= self.max_queue_size
        queue.append(message)

        if was_full:
            self._dropped_count[execution_id] += 1
            if self._dropped_count[execution_id] % 100 == 1:
                logger.warning(f"消息队列溢出: {execution_id}, 已丢弃 {self._dropped_count[execution_id]} 条")

        # 确保处理任务在运行
        if execution_id not in self._processing or self._processing[execution_id].done():
            self._processing[execution_id] = asyncio.create_task(
                self._process_queue(execution_id)
            )

        return True

    async def _process_queue(self, execution_id: str):
        """处理队列（批量发送）"""
        try:
            while True:
                queue = self._queues.get(execution_id)
                if not queue:
                    break

                # 收集一批消息
                batch = []
                while queue and len(batch) < self.batch_size:
                    try:
                        batch.append(queue.popleft())
                    except IndexError:
                        break

                if batch:
                    from src.services.websockets.websocket_connection_manager import websocket_manager
                    await websocket_manager._broadcast_batch(execution_id, batch)

                # 如果队列空了，等待一小段时间看是否有新消息
                if not queue:
                    await asyncio.sleep(self.flush_interval)
                    if not self._queues.get(execution_id):
                        break
                else:
                    # 队列还有消息，立即继续处理
                    await asyncio.sleep(0)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"处理消息队列失败: {execution_id}, {e}")
        finally:
            self._processing.pop(execution_id, None)

    def get_queue_size(self, execution_id: str) -> int:
        """获取队列大小"""
        return len(self._queues.get(execution_id, []))

    def get_total_queue_size(self) -> int:
        """获取总队列大小"""
        return sum(len(q) for q in self._queues.values())

    def get_dropped_count(self, execution_id: str = None) -> int:
        """获取丢弃消息数"""
        if execution_id:
            return self._dropped_count.get(execution_id, 0)
        return sum(self._dropped_count.values())


class WebSocketConnectionManager:
    """WebSocket连接管理器 - 生产环境优化版本"""

    def __init__(
        self,
        max_connections_per_execution: int = 50,
        ping_interval: float = 30.0,
        pong_timeout: float = 10.0,
        max_missed_pongs: int = 3,
        cleanup_interval: float = 300.0,
        inactive_timeout: float = 1800.0
    ):
        self.connection_pool = ConnectionPool(max_connections_per_execution)
        self.message_queue = MessageQueue()
        self.heartbeat_manager = HeartbeatManager(ping_interval, pong_timeout, max_missed_pongs)

        self.cleanup_interval = cleanup_interval
        self.inactive_timeout = inactive_timeout

        # 统计信息
        self._stats = {
            "total_connections": 0,
            "total_disconnections": 0,
            "messages_sent": 0,
            "messages_received": 0,
            "bytes_sent": 0,
            "bytes_received": 0,
            "errors_count": 0,
            "heartbeat_timeouts": 0,
            "start_time": datetime.now(timezone.utc)
        }

        self._cleanup_task: Optional[asyncio.Task] = None
        self._started = False

    async def start(self):
        """启动管理器"""
        if self._started:
            return

        self._started = True

        # 启动心跳管理器
        await self.heartbeat_manager.start(
            self.connection_pool,
            self._handle_heartbeat_timeout
        )

        # 启动清理任务
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        logger.info("WebSocket连接管理器已启动")

    async def shutdown(self):
        """关闭管理器"""
        logger.info("正在关闭WebSocket连接管理器...")

        self._started = False

        # 停止心跳
        await self.heartbeat_manager.stop()

        # 停止清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 关闭所有连接
        all_connections = self.connection_pool.get_all_connections()
        for execution_id, connections in all_connections.items():
            for conn in connections:
                try:
                    await conn.websocket.close(code=1001, reason="服务器关闭")
                except Exception:
                    pass

        logger.info("WebSocket连接管理器已关闭")

    def _generate_connection_id(self, execution_id: str, websocket: WebSocket) -> str:
        """生成连接ID"""
        return f"{execution_id}_{id(websocket)}_{time.time_ns()}"

    async def connect(
        self,
        websocket: WebSocket,
        execution_id: str,
        user_id: int
    ) -> str:
        """建立WebSocket连接"""
        # 确保管理器已启动
        if not self._started:
            await self.start()

        connection_id = self._generate_connection_id(execution_id, websocket)

        try:
            # 接受连接
            await websocket.accept()

            # 创建连接信息
            conn_info = ConnectionInfo(
                connection_id=connection_id,
                execution_id=execution_id,
                user_id=user_id,
                websocket=websocket
            )

            # 添加到连接池
            await self.connection_pool.add_connection(conn_info)

            # 更新统计
            self._stats["total_connections"] += 1

            logger.info(f"WebSocket连接建立: {connection_id} (执行ID: {execution_id}, 用户: {user_id})")

            # 发送连接确认
            await self._send_direct(websocket, {
                "type": "connected",
                "connection_id": connection_id,
                "execution_id": execution_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config": {
                    "ping_interval": self.heartbeat_manager.ping_interval,
                    "pong_timeout": self.heartbeat_manager.pong_timeout
                }
            })

            return connection_id

        except Exception as e:
            self._stats["errors_count"] += 1
            logger.error(f"WebSocket连接建立失败: {e}")
            raise

    async def disconnect(self, websocket: WebSocket, execution_id: str):
        """断开WebSocket连接"""
        # 查找连接
        connections = self.connection_pool.get_connections(execution_id)
        for conn in connections:
            if conn.websocket == websocket:
                await self.connection_pool.remove_connection(execution_id, conn.connection_id)
                self._stats["total_disconnections"] += 1
                logger.info(f"WebSocket连接断开: {conn.connection_id}")
                return

    async def _handle_heartbeat_timeout(self, conn: ConnectionInfo):
        """处理心跳超时"""
        self._stats["heartbeat_timeouts"] += 1

        try:
            await conn.websocket.close(code=4008, reason="心跳超时")
        except Exception:
            pass

        await self.connection_pool.remove_connection(conn.execution_id, conn.connection_id)
        logger.warning(f"连接因心跳超时断开: {conn.connection_id}")

    async def _cleanup_loop(self):
        """清理循环"""
        while self._started:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_inactive_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理任务异常: {e}")

    async def _cleanup_inactive_connections(self):
        """清理不活跃连接"""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.inactive_timeout)
        cleaned = 0

        all_connections = self.connection_pool.get_all_connections()
        for execution_id, connections in all_connections.items():
            for conn in connections:
                if conn.last_activity < cutoff:
                    try:
                        await conn.websocket.close(code=4009, reason="连接不活跃")
                    except Exception:
                        pass
                    await self.connection_pool.remove_connection(execution_id, conn.connection_id)
                    cleaned += 1

        if cleaned > 0:
            logger.info(f"清理了 {cleaned} 个不活跃连接")

    async def handle_client_message(self, execution_id: str, connection_id: str, message: dict):
        """处理客户端消息"""
        conn = self.connection_pool.get_connection(execution_id, connection_id)
        if not conn:
            return

        # 更新活动时间
        conn.last_activity = datetime.now(timezone.utc)
        conn.messages_received += 1
        self._stats["messages_received"] += 1

        message_type = message.get("type")

        if message_type == "pong":
            # 心跳响应
            self.heartbeat_manager.record_pong(conn)
        elif message_type == "ping":
            # 客户端主动 ping
            await self._send_direct(conn.websocket, {
                "type": "pong",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
        else:
            # 其他消息类型
            logger.debug(f"收到客户端消息: {message_type}")

    async def broadcast_to_execution(self, execution_id: str, message: dict):
        """向执行ID广播消息（通过队列）"""
        await self.message_queue.enqueue(execution_id, message)

    async def _broadcast_batch(self, execution_id: str, messages: List[dict]):
        """批量广播消息（并发发送）"""
        connections = self.connection_pool.get_connections(execution_id)
        if not connections:
            return

        # 预序列化消息（避免重复序列化）
        serialized_messages = []
        total_bytes = 0
        for message in messages:
            msg_str = to_json(message)
            serialized_messages.append(msg_str)
            total_bytes += len(msg_str.encode('utf-8'))

        # 并发发送到所有连接
        async def send_to_connection(conn: ConnectionInfo) -> Optional[ConnectionInfo]:
            if conn.state != ConnectionState.CONNECTED:
                return None
            try:
                for msg_str in serialized_messages:
                    await asyncio.wait_for(
                        conn.websocket.send_text(msg_str),
                        timeout=5.0
                    )
                conn.messages_sent += len(messages)
                conn.bytes_sent += total_bytes
                return None
            except asyncio.TimeoutError:
                logger.warning(f"发送消息超时: {conn.connection_id}")
                return conn
            except Exception as e:
                logger.debug(f"广播消息失败: {conn.connection_id}, {e}")
                return conn

        # 并发执行
        tasks = [send_to_connection(conn) for conn in connections]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 统计和清理
        sent_count = 0
        for result in results:
            if result is None:
                sent_count += 1
            elif isinstance(result, ConnectionInfo):
                await self.connection_pool.remove_connection(result.execution_id, result.connection_id)

        self._stats["messages_sent"] += len(messages) * sent_count

    async def _send_direct(self, websocket: WebSocket, message: dict):
        """直接发送消息"""
        message_str = to_json(message)
        await websocket.send_text(message_str)

    # ==================== 便捷方法 ====================

    async def send_log_message(
        self,
        execution_id: str,
        log_type: str,
        content: str,
        level: str = "INFO",
        source: str = None
    ):
        """发送日志消息"""
        message = {
            "type": "log_line",
            "execution_id": execution_id,
            "data": {
                "execution_id": execution_id,
                "log_type": log_type,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "source": source or "task_execution"
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await self.broadcast_to_execution(execution_id, message)

    async def send_execution_status(
        self,
        execution_id: str,
        status: str,
        progress: float = None,
        message: str = None
    ):
        """发送执行状态"""
        status_message = {
            "type": "execution_status",
            "execution_id": execution_id,
            "data": {
                "status": status,
                "progress": progress,
                "message": message
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await self.broadcast_to_execution(execution_id, status_message)

    async def send_historical_logs_start(self, execution_id: str):
        """发送历史日志开始标记"""
        await self.broadcast_to_execution(execution_id, {
            "type": "historical_logs_start",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    async def send_historical_logs_end(self, execution_id: str, sent_lines: int):
        """发送历史日志结束标记"""
        await self.broadcast_to_execution(execution_id, {
            "type": "historical_logs_end",
            "sent_lines": sent_lines,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    async def send_no_historical_logs(self, execution_id: str):
        """发送无历史日志标记"""
        await self.broadcast_to_execution(execution_id, {
            "type": "no_historical_logs",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    def get_connections_for_execution(self, execution_id: str) -> int:
        """获取执行ID的连接数"""
        return self.connection_pool.get_connection_count(execution_id)

    def get_stats(self) -> dict:
        """获取统计信息"""
        uptime = (datetime.now(timezone.utc) - self._stats["start_time"]).total_seconds()

        # 计算吞吐量
        messages_per_second = self._stats["messages_sent"] / uptime if uptime > 0 else 0
        bytes_per_second = self._stats.get("bytes_sent", 0) / uptime if uptime > 0 else 0

        return {
            **self._stats,
            "uptime_seconds": round(uptime, 2),
            "active_connections": self.connection_pool.get_total_connection_count(),
            "active_executions": len(self.connection_pool.get_all_connections()),
            "queued_messages": self.message_queue.get_total_queue_size(),
            "dropped_messages": self.message_queue.get_dropped_count(),
            "messages_per_second": round(messages_per_second, 2),
            "bytes_per_second": round(bytes_per_second, 2),
            "health": "healthy" if self._started else "stopped"
        }


# 创建全局实例
websocket_manager = WebSocketConnectionManager()
