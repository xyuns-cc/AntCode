"""系统指标缓存服务"""

import asyncio
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime

import psutil
from loguru import logger

from src.infrastructure.cache.cache import metrics_cache
from src.core.config import settings
from src.schemas.scheduler import SystemMetricsResponse


@dataclass
class SystemMetrics:
    cpu_percent: float
    cpu_cores: int | None
    memory_percent: float
    memory_total: int | None
    memory_used: int | None
    memory_available: int | None
    disk_percent: float  # 统一命名：改为 disk_percent
    disk_total: int | None
    disk_used: int | None
    disk_free: int | None
    active_tasks: int
    uptime_seconds: int | None
    collected_at: datetime

    def to_response(self):
        return SystemMetricsResponse(
            cpu_percent=self.cpu_percent,
            cpu_cores=self.cpu_cores,
            memory_percent=self.memory_percent,
            memory_total=self.memory_total,
            memory_used=self.memory_used,
            memory_available=self.memory_available,
            disk_percent=self.disk_percent,  # 统一使用 disk_percent
            disk_total=self.disk_total,
            disk_used=self.disk_used,
            disk_free=self.disk_free,
            active_tasks=self.active_tasks,
            uptime_seconds=self.uptime_seconds,
        )


class SystemMetricsService:
    """统一缓存系统指标服务"""

    CACHE_KEY = "metrics:system"

    def __init__(self):
        self._update_task = None

    @staticmethod
    def _is_valid_percent(value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False
        return not math.isnan(numeric) and not math.isinf(numeric)

    @staticmethod
    def _normalize_percent(value, default=0.0):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return round(default, 2)

        if math.isnan(numeric) or math.isinf(numeric):
            numeric = default

        numeric = max(0.0, min(100.0, numeric))
        return round(numeric, 2)

    async def _collect_cpu_metrics(self):
        try:
            await asyncio.to_thread(psutil.cpu_percent, None)
            cpu_percent_sample = await asyncio.to_thread(psutil.cpu_percent, 0.5)
        except Exception:
            return 0.0, None

        if not self._is_valid_percent(cpu_percent_sample):
            try:
                cpu_percent_sample = await asyncio.to_thread(psutil.cpu_percent, 1.0)
            except Exception:
                cpu_percent_sample = 0.0
        cpu_percent = self._normalize_percent(cpu_percent_sample)
        try:
            cpu_cores = await asyncio.to_thread(psutil.cpu_count, True)
        except Exception:
            cpu_cores = None

        if cpu_percent <= 0.0:
            try:
                load_avg = await asyncio.to_thread(psutil.getloadavg)
                if load_avg:
                    cpu_percent = self._normalize_percent(
                        (load_avg[0] / max(cpu_cores or 1, 1)) * 100.0,
                        default=cpu_percent,
                    )
            except (AttributeError, OSError):
                pass

        return cpu_percent, cpu_cores

    async def _collect_memory_metrics(self):
        vm = await asyncio.to_thread(psutil.virtual_memory)
        return {
            "percent": self._normalize_percent(vm.percent),
            "total": int(vm.total),
            "used": int(vm.used),
            "available": int(vm.available),
        }

    async def _collect_disk_metrics(self):
        du = await asyncio.to_thread(psutil.disk_usage, '/')
        return {
            "percent": self._normalize_percent(du.percent),
            "total": int(du.total),
            "used": int(du.used),
            "free": int(du.free),
        }

    async def _collect_active_tasks(self):
        try:
            from src.services.scheduler.scheduler_service import scheduler_service
            return await asyncio.to_thread(lambda: len(scheduler_service.running_tasks))
        except Exception:
            return 0

    async def _collect_uptime_seconds(self):
        try:
            current_time, boot_time = await asyncio.gather(
                asyncio.to_thread(time.time),
                asyncio.to_thread(psutil.boot_time),
            )
            return int(current_time - boot_time)
        except Exception:
            return None

    async def _collect_metrics(self):
        try:
            (
                (cpu_percent, cpu_cores),
                memory_metrics,
                disk_metrics,
                active_tasks,
                uptime_seconds,
            ) = await asyncio.gather(
                self._collect_cpu_metrics(),
                self._collect_memory_metrics(),
                self._collect_disk_metrics(),
                self._collect_active_tasks(),
                self._collect_uptime_seconds(),
            )

            return SystemMetrics(
                cpu_percent=round(cpu_percent, 2),
                cpu_cores=cpu_cores,
                memory_percent=round(memory_metrics["percent"], 2),
                memory_total=memory_metrics["total"],
                memory_used=memory_metrics["used"],
                memory_available=memory_metrics["available"],
                disk_percent=round(disk_metrics["percent"], 2),  # 统一使用 disk_percent
                disk_total=disk_metrics["total"],
                disk_used=disk_metrics["used"],
                disk_free=disk_metrics["free"],
                active_tasks=active_tasks,
                uptime_seconds=uptime_seconds,
                collected_at=datetime.now()
            )
        except Exception as e:
            logger.error(f"收集指标失败: {e}")
            return SystemMetrics(
                cpu_percent=0.0,
                cpu_cores=None,
                memory_percent=0.0,
                memory_total=None,
                memory_used=None,
                memory_available=None,
                disk_percent=0.0,  # 统一使用 disk_percent
                disk_total=None,
                disk_used=None,
                disk_free=None,
                active_tasks=0,
                uptime_seconds=None,
                collected_at=datetime.now()
            )

    async def get_metrics(self, force_refresh=False):
        if not force_refresh:
            try:
                cached_metrics = await metrics_cache.get(self.CACHE_KEY)
                if cached_metrics:
                    logger.debug("指标缓存命中")
                    return SystemMetrics(**cached_metrics).to_response()
            except Exception as e:
                logger.warning(f"指标缓存读取失败: {e}")

        logger.debug("正在收集系统指标")
        metrics = await self._collect_metrics()

        try:
            await metrics_cache.set(self.CACHE_KEY, asdict(metrics))
        except Exception as e:
            logger.warning(f"指标缓存写入失败: {e}")

        return metrics.to_response()

    async def start_background_update(self, update_interval=None):
        if self._update_task and not self._update_task.done():
            return

        if update_interval is None:
            update_interval = max(10, settings.METRICS_CACHE_TTL // 2)

        async def background_updater():
            logger.info(f"指标后台更新已启动 (间隔: {update_interval}s)")
            while True:
                try:
                    await self.get_metrics(force_refresh=True)
                    await asyncio.sleep(update_interval)
                except asyncio.CancelledError:
                    logger.info("指标后台更新已停止")
                    break
                except Exception as e:
                    logger.error(f"后台指标更新失败: {e}")
                    await asyncio.sleep(update_interval)

        self._update_task = asyncio.create_task(background_updater())

    async def stop_background_update(self):
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

    async def clear_cache(self):
        try:
            await metrics_cache.clear_prefix("metrics:")
            logger.info("指标缓存已清除")
        except Exception as e:
            logger.error(f"清除指标缓存失败: {e}")
            raise

    async def get_cache_info(self):
        try:
            cache_stats = await metrics_cache.get_stats()

            try:
                cached_metrics = await metrics_cache.get(self.CACHE_KEY)
                cache_valid = cached_metrics is not None
            except Exception as e:
                logger.warning(f"缓存有效性检查失败: {e}")
                cache_valid = False

            return {
                **cache_stats,
                "cache_valid": cache_valid,
                "background_update_running": self._update_task and not self._update_task.done(),
                "cache_key": self.CACHE_KEY
            }
        except Exception as e:
            logger.error(f"获取缓存信息失败: {e}")
            return {
                "name": "metrics",
                "cache_valid": False,
                "background_update_running": self._update_task and not self._update_task.done(),
                "cache_key": self.CACHE_KEY,
                "error": str(e)
            }


system_metrics_service = SystemMetricsService()
metrics_cache_service = system_metrics_service
