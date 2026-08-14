"""系统指标缓存服务"""

import asyncio
import contextlib
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime

import psutil
from loguru import logger

from antcode_core.common.config import settings
from antcode_core.domain.schemas.task import SystemMetricsResponse
from antcode_core.infrastructure.cache.cache import metrics_cache

# psutil.cpu_percent 需要一段阻塞采样窗口才能给出真实占用；
# 短窗口偶发返回 NaN，此时用更长的窗口重采一次。
CPU_SAMPLE_SECONDS = 0.5
CPU_RESAMPLE_SECONDS = 1.0
# 百分比满量程：既是裁剪上限，也是比率换算系数。
PERCENT_FULL = 100.0
PERCENT_DECIMALS = 2


class MetricsCollectionError(RuntimeError):
    """系统指标采集失败。

    采集链路任意一环失败都必须抛出本异常：调用方必须能区分"采集不到"与
    "真的是 0"。用零值冒充采集结果会让监控在最该告警的时刻呈现完美健康。
    """


@dataclass
class SystemMetrics:
    cpu_percent: float
    cpu_cores: int
    memory_percent: float
    memory_total: int
    memory_used: int
    memory_available: int
    disk_percent: float  # 统一命名：改为 disk_percent
    disk_total: int
    disk_used: int
    disk_free: int
    active_tasks: int
    uptime_seconds: int
    collected_at: datetime
    # 新增字段
    queue_size: int = 0  # 待执行任务队列大小
    success_rate: float = 0.0  # 今日任务成功率（百分比）

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
            queue_size=self.queue_size,
            success_rate=self.success_rate,
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
    def _normalize_percent(value):
        """裁剪到 [0, 100] 并保留两位小数；非有限数值一律视为采集失败。"""
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise MetricsCollectionError(f"百分比指标不是数值: {value!r}") from exc

        if math.isnan(numeric) or math.isinf(numeric):
            raise MetricsCollectionError(f"百分比指标不是有限值: {numeric!r}")

        return round(max(0.0, min(PERCENT_FULL, numeric)), PERCENT_DECIMALS)

    async def _collect_cpu_metrics(self):
        """采集 CPU 占用与核心数。

        核心数缺失时必须抛错：cpu_cores=0 会让下游"每核占用"之类的换算除零。
        """
        await asyncio.to_thread(psutil.cpu_percent, None)
        sample = await asyncio.to_thread(psutil.cpu_percent, CPU_SAMPLE_SECONDS)
        if not self._is_valid_percent(sample):
            sample = await asyncio.to_thread(psutil.cpu_percent, CPU_RESAMPLE_SECONDS)

        cpu_cores = await asyncio.to_thread(psutil.cpu_count, True)
        if not cpu_cores:
            raise MetricsCollectionError("psutil 未返回可用的 CPU 核心数")

        return self._normalize_percent(sample), int(cpu_cores)

    async def _collect_memory_metrics(self):
        vm = await asyncio.to_thread(psutil.virtual_memory)
        return {
            "percent": self._normalize_percent(vm.percent),
            "total": int(vm.total),
            "used": int(vm.used),
            "available": int(vm.available),
        }

    async def _collect_disk_metrics(self):
        du = await asyncio.to_thread(psutil.disk_usage, "/")
        return {
            "percent": self._normalize_percent(du.percent),
            "total": int(du.total),
            "used": int(du.used),
            "free": int(du.free),
        }

    async def _collect_active_tasks(self):
        from antcode_core.domain.models.enums import TaskStatus
        from antcode_core.domain.models.task_run import TaskRun

        return await TaskRun.filter(status=TaskStatus.RUNNING).count()

    async def _collect_uptime_seconds(self):
        boot_time = await asyncio.to_thread(psutil.boot_time)
        return int(time.time() - boot_time)

    async def _collect_queue_size(self):
        """收集待执行任务队列大小（pending 状态的任务数）"""
        from antcode_core.domain.models.enums import TaskStatus
        from antcode_core.domain.models.task import Task

        return await Task.filter(status=TaskStatus.PENDING, is_active=True).count()

    async def _collect_success_rate(self):
        """收集今日任务成功率；今日无已完成执行时 0.0 是真实语义而非兜底值。"""
        from antcode_core.domain.models.enums import TaskStatus
        from antcode_core.domain.models.task_run import TaskRun

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # 今日所有已完成的执行记录（成功或失败）
        total_today = await TaskRun.filter(
            start_time__gte=today_start, status__in=[TaskStatus.SUCCESS, TaskStatus.FAILED]
        ).count()
        if total_today == 0:
            return 0.0

        success_today = await TaskRun.filter(start_time__gte=today_start, status=TaskStatus.SUCCESS).count()
        return round(success_today / total_today * PERCENT_FULL, PERCENT_DECIMALS)

    async def _collect_metrics(self):
        """并行采集全部指标；任一环失败立即抛错，绝不返回零值伪装成健康数据。"""
        try:
            (
                (cpu_percent, cpu_cores),
                memory_metrics,
                disk_metrics,
                active_tasks,
                uptime_seconds,
                queue_size,
                success_rate,
            ) = await asyncio.gather(
                self._collect_cpu_metrics(),
                self._collect_memory_metrics(),
                self._collect_disk_metrics(),
                self._collect_active_tasks(),
                self._collect_uptime_seconds(),
                self._collect_queue_size(),
                self._collect_success_rate(),
            )
        except Exception as e:
            logger.error(f"收集指标失败: {e}")
            raise MetricsCollectionError(f"系统指标采集失败: {e}") from e

        return SystemMetrics(
            cpu_percent=cpu_percent,
            cpu_cores=cpu_cores,
            memory_percent=memory_metrics["percent"],
            memory_total=memory_metrics["total"],
            memory_used=memory_metrics["used"],
            memory_available=memory_metrics["available"],
            disk_percent=disk_metrics["percent"],  # 统一使用 disk_percent
            disk_total=disk_metrics["total"],
            disk_used=disk_metrics["used"],
            disk_free=disk_metrics["free"],
            active_tasks=active_tasks,
            uptime_seconds=uptime_seconds,
            collected_at=datetime.now(),
            queue_size=queue_size,
            success_rate=success_rate,
        )

    async def get_metrics(self, force_refresh=False):
        """返回系统指标；采集失败时抛 MetricsCollectionError，不写缓存也不返回旧值。"""
        if not force_refresh:
            cached_metrics = await metrics_cache.get(self.CACHE_KEY)
            if cached_metrics:
                logger.debug("指标缓存命中")
                return SystemMetrics(**cached_metrics).to_response()

        logger.debug("正在收集系统指标")
        metrics = await self._collect_metrics()
        await metrics_cache.set(self.CACHE_KEY, asdict(metrics))
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
                    # 采集失败不写缓存：旧值会随 TTL 自然过期，
                    # 之后的读请求会重新采集并把失败抛给调用方。
                    logger.error(f"后台指标更新失败: {e}")
                    await asyncio.sleep(update_interval)

        self._update_task = asyncio.create_task(background_updater())

    async def stop_background_update(self):
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._update_task
            self._update_task = None

    async def clear_cache(self):
        await metrics_cache.clear_prefix("metrics:")
        logger.info("指标缓存已清除")

    async def get_cache_info(self):
        """返回缓存统计；查询失败直接抛错，不返回 cache_valid=False 的伪造结果。"""
        cache_stats = await metrics_cache.get_stats()
        cached_metrics = await metrics_cache.get(self.CACHE_KEY)
        return {
            **cache_stats,
            "cache_valid": cached_metrics is not None,
            "background_update_running": bool(self._update_task and not self._update_task.done()),
            "cache_key": self.CACHE_KEY,
        }


system_metrics_service = SystemMetricsService()
metrics_cache_service = system_metrics_service
