"""
内存管理优化工具
提供内存监控、限制和优化功能
"""

import asyncio
import gc
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import psutil
from loguru import logger


@dataclass
class MemoryStats:
    """内存统计信息"""
    process_memory_mb: float
    system_memory_percent: float
    available_memory_mb: float
    gc_counts: dict
    timestamp: datetime


class MemoryMonitor:
    """内存监控器"""

    def __init__(self, warning_threshold = 80.0, critical_threshold = 90.0):
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.stats_history: List[MemoryStats] = []
        self.max_history = 100
        self._callbacks: List[Callable[[MemoryStats], None]] = []
        self._process = psutil.Process(os.getpid())

    def add_callback(self, callback):
        """添加内存统计回调"""
        self._callbacks.append(callback)

    def get_current_stats(self):
        """获取当前内存统计"""
        # 进程内存使用
        memory_info = self._process.memory_info()
        process_memory_mb = memory_info.rss / 1024 / 1024

        # 系统内存使用
        system_memory = psutil.virtual_memory()
        system_memory_percent = system_memory.percent
        available_memory_mb = system_memory.available / 1024 / 1024

        # GC统计
        gc_counts = {i: gc.get_count()[i] for i in range(len(gc.get_count()))}

        stats = MemoryStats(
            process_memory_mb=process_memory_mb,
            system_memory_percent=system_memory_percent,
            available_memory_mb=available_memory_mb,
            gc_counts=gc_counts,
            timestamp=datetime.now()
        )

        # 保存历史记录
        self.stats_history.append(stats)
        if len(self.stats_history) > self.max_history:
            self.stats_history.pop(0)

        # 检查阈值并执行回调
        self._check_thresholds(stats)

        return stats

    def _check_thresholds(self, stats):
        """检查内存阈值"""
        if stats.system_memory_percent >= self.critical_threshold:
            logger.critical(f"内存使用严重: {stats.system_memory_percent:.1f}%")
            # 使用 asyncio.create_task 异步执行清理
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_memory_cleanup())
            except RuntimeError:
                # 没有运行中的事件循环，同步清理
                self._sync_memory_cleanup()
        elif stats.system_memory_percent >= self.warning_threshold:
            logger.warning(f"内存使用警告: {stats.system_memory_percent:.1f}%")

        # 执行回调
        for callback in self._callbacks:
            try:
                callback(stats)
            except Exception as e:
                logger.error(f"内存监控回调失败: {e}")

    def _sync_memory_cleanup(self):
        """同步内存清理（仅 GC）"""
        logger.info("触发同步内存清理...")
        collected = gc.collect()
        logger.info(f"垃圾回收完成，清理了 {collected} 个对象")

    async def _async_memory_cleanup(self):
        """异步内存清理（包括缓存）"""
        logger.info("触发异步内存清理...")

        # 强制垃圾回收
        collected = gc.collect()
        logger.info(f"垃圾回收完成，清理了 {collected} 个对象")

        # 异步清理缓存
        try:
            from src.infrastructure.cache import cache_manager
            for name in cache_manager.list_caches():
                cache = cache_manager.get_cache(name)
                # 清理过期的内存缓存项
                if hasattr(cache, '_cleanup_memory_cache'):
                    cache._cleanup_memory_cache()
            logger.info("缓存清理完成")
        except Exception as e:
            logger.warning(f"缓存清理失败: {e}")

    def _trigger_memory_cleanup(self):
        """触发内存清理（兼容旧接口）"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_memory_cleanup())
        except RuntimeError:
            self._sync_memory_cleanup()

    def get_memory_trend(self, minutes = 30):
        """获取内存使用趋势"""
        cutoff_time = datetime.now() - timedelta(minutes=minutes)
        recent_stats = [
            s for s in self.stats_history 
            if s.timestamp >= cutoff_time
        ]

        if not recent_stats:
            return {"error": "没有足够的历史数据"}

        process_memories = [s.process_memory_mb for s in recent_stats]
        system_memories = [s.system_memory_percent for s in recent_stats]

        return {
            "timeframe_minutes": minutes,
            "sample_count": len(recent_stats),
            "process_memory": {
                "current_mb": process_memories[-1],
                "min_mb": min(process_memories),
                "max_mb": max(process_memories),
                "avg_mb": sum(process_memories) / len(process_memories)
            },
            "system_memory": {
                "current_percent": system_memories[-1],
                "min_percent": min(system_memories),
                "max_percent": max(system_memories),
                "avg_percent": sum(system_memories) / len(system_memories)
            }
        }


class StreamingBuffer:
    """流式缓冲区，用于大数据处理"""

    def __init__(self, max_size = 8 * 1024 * 1024):  # 8MB
        self.max_size = max_size
        self.buffer = bytearray()
        self.overflow_callback: Optional[Callable[[bytes], None]] = None

    def write(self, data):
        """写入数据"""
        if len(self.buffer) + len(data) > self.max_size:
            if self.overflow_callback:
                # 处理溢出数据
                overflow_data = bytes(self.buffer) + data
                self.overflow_callback(overflow_data)
                self.buffer.clear()
            else:
                # 没有溢出处理器，清空缓冲区
                logger.warning(f"缓冲区溢出，清空 {len(self.buffer)} 字节")
                self.buffer.clear()
                self.buffer.extend(data)
        else:
            self.buffer.extend(data)

    def read(self, size = None):
        """读取数据"""
        if size is None:
            data = bytes(self.buffer)
            self.buffer.clear()
            return data
        else:
            data = bytes(self.buffer[:size])
            del self.buffer[:size]
            return data

    def set_overflow_callback(self, callback):
        """设置溢出回调"""
        self.overflow_callback = callback

    def size(self):
        """获取缓冲区大小"""
        return len(self.buffer)

    def clear(self):
        """清空缓冲区"""
        self.buffer.clear()


# 全局内存管理实例
memory_monitor = MemoryMonitor()


def memory_optimized(max_memory_mb = 100):
    """内存优化装饰器"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 检查内存使用
            stats = memory_monitor.get_current_stats()

            if stats.process_memory_mb > max_memory_mb:
                logger.warning(f"内存使用过高: {stats.process_memory_mb:.1f}MB > {max_memory_mb}MB")
                # 触发内存清理
                memory_monitor._trigger_memory_cleanup()

            try:
                result = await func(*args, **kwargs)
                return result
            except MemoryError:
                logger.error(f"内存不足，执行 {func.__name__} 失败")
                memory_monitor._trigger_memory_cleanup()
                raise

        return wrapper
    return decorator


async def setup_memory_monitoring():
    """设置内存监控"""
    async def monitoring_loop():
        while True:
            try:
                stats = memory_monitor.get_current_stats()
                logger.debug(
                    f"内存使用: 进程 {stats.process_memory_mb:.1f}MB, "
                    f"系统 {stats.system_memory_percent:.1f}%"
                )
                await asyncio.sleep(60)  # 每分钟检查一次
            except Exception as e:
                logger.error(f"内存监控失败: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(monitoring_loop())
    logger.info("内存监控已启动")