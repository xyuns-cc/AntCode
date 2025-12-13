"""资源监控和自适应调整服务"""

import asyncio
import time
from typing import Dict, Optional

import psutil
from loguru import logger

from ..config import get_node_config


class ResourceMonitor:
    """资源监控器，动态调整任务资源限制"""

    def __init__(self):
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._last_adjustment = 0
        self._adjustment_cooldown = 60  # 调整冷却时间（秒）

        # 性能历史（用于趋势分析）
        self._cpu_history: list = []
        self._memory_history: list = []
        self._max_history = 10

    async def start_monitoring(self):
        """启动资源监控"""
        if self._monitoring:
            return

        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("资源监控已启动")

    async def stop_monitoring(self):
        """停止资源监控"""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("资源监控已停止")

    async def _monitor_loop(self):
        """监控循环"""
        while self._monitoring:
            try:
                await self._check_and_adjust()
                await asyncio.sleep(30)  # 每30秒检查一次
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"资源监控异常: {e}")
                await asyncio.sleep(60)

    async def _check_and_adjust(self):
        """检查资源使用并调整限制"""
        config = get_node_config()
        if not config.auto_resource_limit:
            return

        # 获取当前资源使用情况
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        # 更新历史记录
        self._cpu_history.append(cpu_percent)
        self._memory_history.append(memory_percent)
        if len(self._cpu_history) > self._max_history:
            self._cpu_history.pop(0)
            self._memory_history.pop(0)

        # 计算平均使用率
        avg_cpu = sum(self._cpu_history) / len(self._cpu_history)
        avg_memory = sum(self._memory_history) / len(self._memory_history)

        # 检查是否需要调整
        current_time = time.time()
        if current_time - self._last_adjustment < self._adjustment_cooldown:
            return

        adjustment_made = False

        # CPU 使用率过高，减少并发数
        if avg_cpu > 85 and config.max_concurrent_tasks > 1:
            new_concurrent = max(1, config.max_concurrent_tasks - 1)
            logger.warning(f"CPU使用率过高({avg_cpu:.1f}%)，降低并发数: {config.max_concurrent_tasks} -> {new_concurrent}")
            config.max_concurrent_tasks = new_concurrent
            adjustment_made = True

        # CPU 使用率较低，可以增加并发数
        elif avg_cpu < 50 and config.max_concurrent_tasks < 10:
            cpu_count = psutil.cpu_count() or 4
            max_allowed = min(10, cpu_count)
            if config.max_concurrent_tasks < max_allowed:
                new_concurrent = config.max_concurrent_tasks + 1
                logger.info(f"CPU使用率较低({avg_cpu:.1f}%)，提高并发数: {config.max_concurrent_tasks} -> {new_concurrent}")
                config.max_concurrent_tasks = new_concurrent
                adjustment_made = True

        # 内存使用率过高，减少单任务内存限制
        if avg_memory > 80:
            current_limit = config.task_memory_limit_mb
            new_limit = max(256, int(current_limit * 0.8))
            if new_limit < current_limit:
                logger.warning(f"内存使用率过高({avg_memory:.1f}%)，降低单任务内存限制: {current_limit}MB -> {new_limit}MB")
                config.task_memory_limit_mb = new_limit
                adjustment_made = True

        if adjustment_made:
            self._last_adjustment = current_time
            await self._notify_engine_config_change()

    async def _notify_engine_config_change(self):
        """通知任务引擎配置已更改"""
        try:
            from ..api.deps import get_engine
            engine = get_engine()
            if engine and hasattr(engine, 'update_config'):
                config = get_node_config()
                await engine.update_config({
                    'max_concurrent_tasks': config.max_concurrent_tasks,
                    'task_memory_limit_mb': config.task_memory_limit_mb,
                    'task_cpu_time_limit_sec': config.task_cpu_time_limit_sec,
                })
        except Exception as e:
            logger.error(f"通知引擎配置更改失败: {e}")

    def get_resource_stats(self) -> Dict:
        """获取资源统计信息"""
        config = get_node_config()
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()

        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_available_gb": round(memory.available / (1024**3), 2),
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "cpu_history_avg": round(sum(self._cpu_history) / len(self._cpu_history), 1) if self._cpu_history else 0,
            "memory_history_avg": round(sum(self._memory_history) / len(self._memory_history), 1) if self._memory_history else 0,
            "current_limits": {
                "max_concurrent_tasks": config.max_concurrent_tasks,
                "task_memory_limit_mb": config.task_memory_limit_mb,
                "task_cpu_time_limit_sec": config.task_cpu_time_limit_sec,
            },
            "auto_adjustment_enabled": config.auto_resource_limit,
            "monitoring_active": self._monitoring,
        }


# 全局实例
resource_monitor = ResourceMonitor()
