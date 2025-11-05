"""
WebSocket连接管理器
负责管理WebSocket连接的生命周期和消息广播
"""
import asyncio
import weakref
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Set, Optional

import ujson
from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger


class ConnectionPool:
    """连接池管理"""
    
    def __init__(self, max_connections_per_execution = 50):
        self.max_connections_per_execution = max_connections_per_execution
        self.connections: Dict[str, List[WebSocket]] = defaultdict(list)
        self._connection_metadata: Dict[str, dict] = {}
        self._weak_refs: Set[weakref.ref] = set()
    
    def add_connection(self, execution_id, websocket, metadata):
        """添加连接"""
        if len(self.connections[execution_id]) >= self.max_connections_per_execution:
            # 移除最旧的连接
            oldest_ws = self.connections[execution_id].pop(0)
            asyncio.create_task(self._close_websocket_safely(oldest_ws))
            logger.warning(f"执行ID {execution_id} 连接数超限，移除最旧连接")
        
        connection_id = f"{execution_id}_{id(websocket)}_{datetime.now().timestamp()}"
        self.connections[execution_id].append(websocket)
        self._connection_metadata[connection_id] = {**metadata, 'websocket': websocket}
        
        # 添加弱引用用于自动清理
        weak_ref = weakref.ref(websocket, lambda ref: self._cleanup_weak_ref(ref))
        self._weak_refs.add(weak_ref)
        
        return connection_id
    
    def remove_connection(self, execution_id, websocket):
        """移除连接"""
        if execution_id in self.connections:
            if websocket in self.connections[execution_id]:
                self.connections[execution_id].remove(websocket)
            
            if not self.connections[execution_id]:
                del self.connections[execution_id]
        
        # 清理元数据
        to_remove = []
        for conn_id, metadata in self._connection_metadata.items():
            if metadata.get('websocket') == websocket:
                to_remove.append(conn_id)
        
        for conn_id in to_remove:
            del self._connection_metadata[conn_id]
    
    def get_connections(self, execution_id):
        """获取连接列表"""
        return self.connections.get(execution_id, [])
    
    def get_all_connections(self):
        """获取所有连接"""
        return dict(self.connections)
    
    def _cleanup_weak_ref(self, ref):
        """清理弱引用"""
        self._weak_refs.discard(ref)
    
    async def _close_websocket_safely(self, websocket):
        """安全关闭WebSocket"""
        try:
            await websocket.close()
        except Exception as e:
            logger.debug(f"关闭WebSocket时忽略异常: {e}")


class MessageQueue:
    """消息队列管理"""
    
    def __init__(self, max_queue_size = 1000):
        self.max_queue_size = max_queue_size
        self.queues: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_queue_size))
        self.processing: Dict[str, bool] = defaultdict(bool)
    
    async def enqueue_message(self, execution_id, message):
        """入队消息"""
        self.queues[execution_id].append(message)
        
        # 如果没在处理，启动处理协程
        if not self.processing[execution_id]:
            asyncio.create_task(self._process_queue(execution_id))
    
    async def _process_queue(self, execution_id):
        """处理队列中的消息"""
        if self.processing[execution_id]:
            return
        
        self.processing[execution_id] = True
        
        try:
            while self.queues[execution_id]:
                message = self.queues[execution_id].popleft()
                
                # 获取该执行ID的所有连接
                from src.services.websockets.websocket_connection_manager import websocket_manager
                await websocket_manager._broadcast_message_direct(execution_id, message)
                
                # 控制发送频率
                await asyncio.sleep(0.001)  # 1ms延迟
                
        except Exception as e:
            logger.error(f"处理消息队列失败 {execution_id}: {e}")
        finally:
            self.processing[execution_id] = False


class WebSocketConnectionManager:
    """WebSocket连接管理器（优化版本）"""
    
    def __init__(self):
        self.connection_pool = ConnectionPool()
        self.message_queue = MessageQueue()
        
        # 统计信息
        self.stats = {
            "total_connections": 0,
            "active_connections": 0,
            "messages_sent": 0,
            "messages_queued": 0,
            "start_time": datetime.now(timezone.utc),
            "bytes_sent": 0,
            "errors_count": 0
        }
        
        # 性能监控
        self._performance_window = deque(maxlen=1000)  # 最近1000条消息的性能数据
        self._cleanup_task: Optional[asyncio.Task] = None
        # 不在初始化时启动清理任务，而是在需要时启动
        # self._start_cleanup_task()
    
    def _start_cleanup_task(self):
        """启动清理任务"""
        try:
            # 检查是否有运行中的事件循环
            loop = asyncio.get_running_loop()
            if self._cleanup_task is None or self._cleanup_task.done():
                self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        except RuntimeError:
            # 没有运行中的事件循环，暂时不启动清理任务
            logger.debug("没有运行中的事件循环，延迟启动清理任务")
            pass
    
    async def _periodic_cleanup(self):
        """定期清理任务"""
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟清理一次
                await self.cleanup_inactive_connections(30)  # 清理30分钟不活跃的连接
                await self._cleanup_performance_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定期清理任务异常: {e}")
    
    async def _cleanup_performance_data(self):
        """清理性能数据"""
        # 清理过期的性能数据
        current_time = datetime.now(timezone.utc)
        cutoff_time = current_time - timedelta(hours=1)
        
        # 这里可以添加性能数据清理逻辑
        logger.debug("性能数据清理完成")
    
    def generate_connection_id(self, execution_id, websocket):
        """生成连接ID"""
        return f"{execution_id}_{id(websocket)}_{datetime.now().timestamp()}"
    
    async def connect(self, websocket, execution_id, user_id):
        """建立WebSocket连接（优化版本）"""
        start_time = datetime.now(timezone.utc)
        
        # 启动清理任务（如果还没有启动）
        self._start_cleanup_task()
        
        try:
            await websocket.accept()
            
            metadata = {
                "execution_id": execution_id,
                "user_id": user_id,
                "connected_at": start_time,
                "last_activity": start_time,
                "messages_received": 0,
                "bytes_received": 0
            }
            
            connection_id = self.connection_pool.add_connection(execution_id, websocket, metadata)
            
            # 更新统计
            self.stats["total_connections"] += 1
            self.stats["active_connections"] = len(self.connection_pool.get_all_connections())
            
            logger.info(f"✅ WebSocket连接建立: {connection_id} (执行ID: {execution_id})")
            
            # 发送连接确认消息
            await self._send_to_connection_direct(websocket, {
                "type": "connected",
                "connection_id": connection_id,
                "execution_id": execution_id,
                "timestamp": start_time.isoformat(),
                "server_time": start_time.isoformat()
            })
            
            # 记录性能数据
            connect_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            self._performance_window.append({
                "operation": "connect",
                "duration": connect_time,
                "timestamp": start_time
            })
            
            return connection_id
            
        except Exception as e:
            self.stats["errors_count"] += 1
            logger.error(f"❌ WebSocket连接建立失败: {e}")
            raise
    
    async def disconnect(self, websocket, execution_id):
        """断开WebSocket连接（优化版本）"""
        try:
            self.connection_pool.remove_connection(execution_id, websocket)
            
            # 更新统计
            self.stats["active_connections"] = len(self.connection_pool.get_all_connections())
            
            logger.info(f"🔌 WebSocket连接断开: {execution_id}")
            
        except Exception as e:
            self.stats["errors_count"] += 1
            logger.error(f"❌ WebSocket断开处理失败: {e}")
    
    async def broadcast_to_execution(self, execution_id, message):
        """向指定执行ID的所有连接广播消息（队列版本）"""
        try:
            # 添加消息到队列
            await self.message_queue.enqueue_message(execution_id, message)
            self.stats["messages_queued"] += 1
            
        except Exception as e:
            self.stats["errors_count"] += 1
            logger.error(f"❌ 消息入队失败: {e}")
    
    async def _broadcast_message_direct(self, execution_id, message):
        """直接广播消息（由队列调用）"""
        connections = self.connection_pool.get_connections(execution_id)
        if not connections:
            return
        
        message_str = ujson.dumps(message, ensure_ascii=False, default=str)
        message_bytes = len(message_str.encode('utf-8'))
        
        disconnected_connections = []
        sent_count = 0
        
        send_start_time = datetime.now(timezone.utc)
        
        # 并发发送消息
        tasks = []
        for websocket in connections:
            task = asyncio.create_task(self._send_to_connection_safe(websocket, message_str))
            tasks.append((websocket, task))
        
        # 等待所有发送完成
        for websocket, task in tasks:
            try:
                await task
                sent_count += 1
                
            except Exception as e:
                logger.warning(f"⚠️ 发送消息失败，准备清理连接: {e}")
                disconnected_connections.append(websocket)
        
        # 清理断开的连接
        for websocket in disconnected_connections:
            self.connection_pool.remove_connection(execution_id, websocket)
        
        # 更新统计
        self.stats["messages_sent"] += sent_count
        self.stats["bytes_sent"] += message_bytes * sent_count
        
        # 记录性能数据
        send_duration = (datetime.now(timezone.utc) - send_start_time).total_seconds()
        self._performance_window.append({
            "operation": "broadcast",
            "duration": send_duration,
            "connections": len(connections),
            "bytes": message_bytes,
            "timestamp": send_start_time
        })
        
        if disconnected_connections:
            self.stats["active_connections"] = len(self.connection_pool.get_all_connections())
    
    async def _send_to_connection_direct(self, websocket, message):
        """向单个连接发送消息（直接版本）"""
        message_str = ujson.dumps(message, ensure_ascii=False, default=str)
        await websocket.send_text(message_str)
    
    async def _send_to_connection_safe(self, websocket, message_str):
        """安全发送消息到连接"""
        try:
            await websocket.send_text(message_str)
        except WebSocketDisconnect:
            raise  # 重新抛出以便上层处理
        except Exception as e:
            logger.debug(f"发送消息异常: {e}")
            raise
    
    # 保留原有的便捷方法
    async def send_log_message(self, execution_id, log_type, content, level = "INFO", source = None):
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
    
    async def send_execution_status(self, execution_id, status, progress = None, message = None):
        """发送执行状态更新"""
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
    
    async def send_historical_logs_start(self, execution_id):
        """发送历史日志开始标记"""
        message = {
            "type": "historical_logs_start",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await self.broadcast_to_execution(execution_id, message)
    
    async def send_historical_logs_end(self, execution_id, sent_lines):
        """发送历史日志结束标记"""
        message = {
            "type": "historical_logs_end",
            "sent_lines": sent_lines,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await self.broadcast_to_execution(execution_id, message)
    
    async def send_no_historical_logs(self, execution_id):
        """发送无历史日志标记"""
        message = {
            "type": "no_historical_logs",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await self.broadcast_to_execution(execution_id, message)
    
    def get_stats(self):
        """获取连接统计信息（增强版本）"""
        uptime = (datetime.now(timezone.utc) - self.stats["start_time"]).total_seconds()
        
        # 计算性能指标
        recent_performance = list(self._performance_window)[-100:]  # 最近100条记录
        
        avg_connect_time = 0
        avg_broadcast_time = 0
        if recent_performance:
            connect_times = [p["duration"] for p in recent_performance if p["operation"] == "connect"]
            broadcast_times = [p["duration"] for p in recent_performance if p["operation"] == "broadcast"]
            
            avg_connect_time = sum(connect_times) / len(connect_times) if connect_times else 0
            avg_broadcast_time = sum(broadcast_times) / len(broadcast_times) if broadcast_times else 0
        
        return {
            **self.stats,
            "uptime_seconds": uptime,
            "active_executions": len(self.connection_pool.get_all_connections()),
            "queued_messages": sum(len(q) for q in self.message_queue.queues.values()),
            "avg_connect_time": round(avg_connect_time * 1000, 2),  # ms
            "avg_broadcast_time": round(avg_broadcast_time * 1000, 2),  # ms
            "performance_samples": len(self._performance_window)
        }
    
    def get_connections_for_execution(self, execution_id):
        """获取指定执行ID的连接数"""
        return len(self.connection_pool.get_connections(execution_id))
    
    async def cleanup_inactive_connections(self, timeout_minutes = 30):
        """清理不活跃的连接（优化版本）"""
        if timeout_minutes <= 0:
            return
        
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(minutes=timeout_minutes)
        
        cleaned_count = 0
        
        try:
            all_connections = self.connection_pool.get_all_connections()
            
            for execution_id, connections in all_connections.items():
                for websocket in connections[:]:  # 复制列表以安全迭代
                    # 这里简化检查，实际中需要从metadata获取last_activity
                    # 由于架构调整，这里需要重新实现检查逻辑
                    pass
                    
            if cleaned_count > 0:
                logger.info(f"🧹 清理了 {cleaned_count} 个不活跃连接")
                
        except Exception as e:
            logger.error(f"❌ 清理连接失败: {e}")
    
    async def shutdown(self):
        """优雅关闭管理器"""
        logger.info("🔄 正在关闭WebSocket连接管理器...")
        
        # 取消清理任务
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # 关闭所有连接
        all_connections = self.connection_pool.get_all_connections()
        for execution_id, connections in all_connections.items():
            for websocket in connections:
                try:
                    await websocket.close(code=1001, reason="服务器关闭")
                except Exception:
                    pass
        
        logger.info("✅ WebSocket连接管理器已关闭")


# 创建全局连接管理器实例
websocket_manager = WebSocketConnectionManager()