"""
系统指标缓存服务
使用统一缓存系统管理系统指标数据
"""

import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

import psutil
from loguru import logger

from src.core.cache import metrics_cache
from src.core.config import settings
from src.schemas.scheduler import SystemMetricsResponse


@dataclass
class SystemMetrics:
    """系统指标数据"""
    cpu_percent: float
    cpu_cores: int | None
    memory_percent: float
    memory_total: int | None
    memory_used: int | None
    memory_available: int | None
    disk_usage: float
    disk_total: int | None
    disk_used: int | None
    disk_free: int | None
    active_tasks: int
    uptime_seconds: int | None
    collected_at: datetime

    def to_response(self):
        """转换为响应格式"""
        return SystemMetricsResponse(
            cpu_percent=self.cpu_percent,
            cpu_cores=self.cpu_cores,
            memory_percent=self.memory_percent,
            memory_total=self.memory_total,
            memory_used=self.memory_used,
            memory_available=self.memory_available,
            disk_usage=self.disk_usage,
            disk_total=self.disk_total,
            disk_used=self.disk_used,
            disk_free=self.disk_free,
            active_tasks=self.active_tasks,
            uptime_seconds=self.uptime_seconds,
        )


class SystemMetricsService:
    """系统指标服务 - 使用统一缓存"""
    
    CACHE_KEY = "system_metrics"
    
    def __init__(self):
        self._update_task: Optional[asyncio.Task] = None
    
    async def _collect_metrics(self):
        """收集系统指标"""
        try:
            # 使用短间隔获取CPU使用率以避免阻塞
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_cores = psutil.cpu_count(logical=True)

            vm = psutil.virtual_memory()
            memory_percent = vm.percent
            memory_total = int(vm.total)
            memory_used = int(vm.used)
            memory_available = int(vm.available)

            du = psutil.disk_usage('/')
            disk_usage = du.percent
            disk_total = int(du.total)
            disk_used = int(du.used)
            disk_free = int(du.free)
            
            # 获取活跃任务数
            try:
                from src.services.scheduler.scheduler_service import scheduler_service
                active_tasks = len(scheduler_service.running_tasks)
            except:
                active_tasks = 0
            
            # 系统运行时长（秒）
            try:
                import time
                uptime_seconds = int(time.time() - psutil.boot_time())
            except Exception:
                uptime_seconds = None

            return SystemMetrics(
                cpu_percent=round(cpu_percent, 2),
                cpu_cores=cpu_cores,
                memory_percent=round(memory_percent, 2),
                memory_total=memory_total,
                memory_used=memory_used,
                memory_available=memory_available,
                disk_usage=round(disk_usage, 2),
                disk_total=disk_total,
                disk_used=disk_used,
                disk_free=disk_free,
                active_tasks=active_tasks,
                uptime_seconds=uptime_seconds,
                collected_at=datetime.now()
            )
        except Exception as e:
            logger.error(f"收集系统指标失败: {e}")
            # 返回默认值
            return SystemMetrics(
                cpu_percent=0.0,
                cpu_cores=None,
                memory_percent=0.0,
                memory_total=None,
                memory_used=None,
                memory_available=None,
                disk_usage=0.0,
                disk_total=None,
                disk_used=None,
                disk_free=None,
                active_tasks=0,
                uptime_seconds=None,
                collected_at=datetime.now()
            )
    
    async def get_metrics(self, force_refresh = False):
        """获取系统指标（带缓存）"""
        if not force_refresh:
            # 尝试从缓存获取
            cached_metrics = await metrics_cache.get(self.CACHE_KEY)
            if cached_metrics:
                logger.debug("系统指标缓存命中")
                return SystemMetrics(**cached_metrics).to_response()
        
        # 缓存失效或强制刷新，重新收集
        logger.debug("重新收集系统指标")
        metrics = await self._collect_metrics()
        
        # 保存到缓存
        await metrics_cache.set(self.CACHE_KEY, asdict(metrics))
        
        return metrics.to_response()
    
    async def start_background_update(self, update_interval = None):
        """启动后台更新任务"""
        if self._update_task and not self._update_task.done():
            return
        
        if update_interval is None:
            update_interval = max(10, settings.METRICS_CACHE_TTL // 2)
        
        async def background_updater():
            logger.info(f"系统指标后台更新已启动（间隔: {update_interval}秒）")
            while True:
                try:
                    await self.get_metrics(force_refresh=True)
                    await asyncio.sleep(update_interval)
                except asyncio.CancelledError:
                    logger.info("系统指标后台更新已停止")
                    break
                except Exception as e:
                    logger.error(f"后台更新系统指标失败: {e}")
                    await asyncio.sleep(update_interval)
        
        self._update_task = asyncio.create_task(background_updater())
    
    async def stop_background_update(self):
        """停止后台更新任务"""
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None
    
    async def clear_cache(self):
        """清除缓存"""
        await metrics_cache.clear()
        logger.info("系统指标缓存已清空")
    
    async def get_cache_info(self):
        """获取缓存信息"""
        cache_stats = await metrics_cache.get_stats()
        
        # 检查当前缓存是否有效
        cached_metrics = await metrics_cache.get(self.CACHE_KEY)
        cache_valid = cached_metrics is not None
        
        return {
            **cache_stats,
            "cache_valid": cache_valid,
            "background_update_running": self._update_task and not self._update_task.done(),
            "cache_key": self.CACHE_KEY
        }


# 全局系统指标服务实例
system_metrics_service = SystemMetricsService()

# 为了兼容性，保持原有的导出名称
metrics_cache_service = system_metrics_service
