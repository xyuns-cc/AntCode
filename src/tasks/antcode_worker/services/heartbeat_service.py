"""
心跳服务

负责定期发送心跳消息，维护与 Master 的连接状态。

Requirements: 11.3
"""

import asyncio
import platform
import time
from datetime import datetime
from typing import Optional, Callable, Awaitable

import psutil
from loguru import logger

from ..domain.models import Heartbeat, Metrics, OSInfo
from ..domain.interfaces import HeartbeatService as IHeartbeatService
from ..domain.events import HeartbeatSent, HeartbeatFailed, event_bus
from ..transport.protocol import TransportProtocol


class HeartbeatServiceImpl(IHeartbeatService):
    """
    心跳服务实现
    
    特性:
    - 定期发送心跳
    - 动态调整心跳间隔
    - 连续失败检测
    - 系统指标收集
    
    Requirements: 11.3
    """

    MIN_INTERVAL = 10
    MAX_INTERVAL = 60
    DEFAULT_INTERVAL = 30
    MAX_CONSECUTIVE_FAILURES = 5

    def __init__(self, transport: TransportProtocol, node_id: str):
        """
        初始化心跳服务
        
        Args:
            transport: 传输协议实例
            node_id: 节点 ID
        """
        self._transport = transport
        self._node_id = node_id
        self._interval = self.DEFAULT_INTERVAL
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_heartbeat_time: Optional[float] = None
        self._consecutive_failures = 0
        self._os_info_cache: Optional[OSInfo] = None
        
        # 回调
        self._on_disconnect: Optional[Callable[[], Awaitable[None]]] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_heartbeat_time(self) -> Optional[float]:
        return self._last_heartbeat_time

    @property
    def interval(self) -> int:
        return self._interval

    def set_disconnect_callback(self, callback: Callable[[], Awaitable[None]]):
        """设置断开连接回调"""
        self._on_disconnect = callback

    async def start(self, interval: int = 30):
        """启动心跳服务"""
        if self._running:
            return

        self._interval = max(self.MIN_INTERVAL, min(interval, self.MAX_INTERVAL))
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"心跳服务已启动: 间隔={self._interval}s")

    async def stop(self):
        """停止心跳服务"""
        self._running = False
        
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        self._task = None
        logger.info("心跳服务已停止")

    async def send_heartbeat(self) -> bool:
        """立即发送一次心跳"""
        if not self._transport.is_connected:
            return False

        try:
            heartbeat = self._build_heartbeat()
            start_time = time.time()
            
            success = await self._transport.send_heartbeat(heartbeat)
            
            latency_ms = (time.time() - start_time) * 1000
            
            if success:
                self._last_heartbeat_time = time.time()
                self._consecutive_failures = 0
                self._adjust_interval(True)
                
                # 发布成功事件
                await event_bus.publish(HeartbeatSent(
                    node_id=self._node_id,
                    latency_ms=latency_ms
                ))
                
                logger.debug(f"心跳成功: 延迟={latency_ms:.1f}ms, 间隔={self._interval}s")
                return True
            else:
                self._consecutive_failures += 1
                self._adjust_interval(False)
                
                # 发布失败事件
                await event_bus.publish(HeartbeatFailed(
                    node_id=self._node_id,
                    error="发送失败",
                    consecutive_failures=self._consecutive_failures
                ))
                
                return False
                
        except Exception as e:
            self._consecutive_failures += 1
            self._adjust_interval(False)
            
            await event_bus.publish(HeartbeatFailed(
                node_id=self._node_id,
                error=str(e),
                consecutive_failures=self._consecutive_failures
            ))
            
            logger.warning(f"心跳异常: {e}")
            return False

    async def _heartbeat_loop(self):
        """心跳循环"""
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                
                if not self._running:
                    break

                success = await self.send_heartbeat()
                
                # 检查连续失败
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    logger.warning(f"心跳连续失败 {self._consecutive_failures} 次，触发断开回调")
                    if self._on_disconnect:
                        try:
                            await self._on_disconnect()
                        except Exception as e:
                            logger.error(f"断开回调异常: {e}")
                    # 重置计数，避免重复触发
                    self._consecutive_failures = 0
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳循环异常: {e}")
                await asyncio.sleep(5)

    def _adjust_interval(self, success: bool):
        """动态调整心跳间隔"""
        if success:
            # 成功时逐渐增加间隔
            self._interval = min(self._interval + 2, self.MAX_INTERVAL)
        else:
            # 失败时立即降低间隔
            self._interval = self.MIN_INTERVAL

    def _build_heartbeat(self) -> Heartbeat:
        """构建心跳消息"""
        return Heartbeat(
            node_id=self._node_id,
            status="online",
            metrics=self._get_metrics(),
            os_info=self._get_os_info(),
            timestamp=datetime.now(),
            capabilities=self._get_capabilities(),
        )

    def _get_metrics(self) -> Metrics:
        """获取系统指标"""
        try:
            memory_info = psutil.virtual_memory()
            disk_info = psutil.disk_usage("/")
            
            # 尝试获取任务统计
            running_tasks = 0
            task_count = 0
            max_concurrent = 5
            
            try:
                from ..config import get_node_config
                from ..api.deps import get_engine
                
                config = get_node_config()
                max_concurrent = config.max_concurrent_tasks if config else 5
                
                engine = get_engine()
                stats = engine.get_stats()
                task_count = stats.get("tasks_received", 0)
                running_tasks = stats.get("executor", {}).get("running", 0)
            except Exception:
                pass

            return Metrics(
                cpu=round(psutil.cpu_percent(interval=0.1), 1),
                memory=round(memory_info.percent, 1),
                disk=round(disk_info.percent, 1),
                running_tasks=running_tasks,
                max_concurrent_tasks=max_concurrent,
                task_count=task_count,
            )
        except Exception as e:
            logger.warning(f"获取指标异常: {e}")
            return Metrics()

    def _get_os_info(self) -> OSInfo:
        """获取操作系统信息"""
        if self._os_info_cache is None:
            self._os_info_cache = OSInfo(
                os_type=platform.system(),
                os_version=platform.release(),
                python_version=platform.python_version(),
                machine_arch=platform.machine(),
            )
        return self._os_info_cache

    def _get_capabilities(self) -> dict:
        """获取节点能力"""
        try:
            from .capability_service import capability_service
            return capability_service.detect_all()
        except Exception:
            return {}
