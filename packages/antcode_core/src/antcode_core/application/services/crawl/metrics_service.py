"""监控指标收集服务

实现 Stream 长度、PEL 大小、去重集合大小等指标的收集和告警。

需求: 9.1, 9.2, 9.3
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from antcode_core.domain.models.enums import Priority
from antcode_core.application.services.base import BaseService
from antcode_core.application.services.crawl.backends import get_queue_backend
from antcode_core.application.services.crawl.backends.dedup_backend import DedupStore, get_dedup_store

# 默认告警阈值
DEFAULT_STREAM_LENGTH_THRESHOLD = 100000  # Stream 长度告警阈值
DEFAULT_PEL_SIZE_THRESHOLD = 10000  # PEL 大小告警阈值
DEFAULT_DEAD_LETTER_THRESHOLD = 1000  # 死信队列告警阈值
DEFAULT_DEDUP_SIZE_THRESHOLD = 10000000  # 去重集合大小告警阈值（1000万）


@dataclass
class QueueMetrics:
    """队列指标数据类"""

    priority: int = 0
    priority_name: str = ""
    stream_length: int = 0
    pending_count: int = 0
    consumers: dict = field(default_factory=dict)


@dataclass
class SystemMetrics:
    """系统指标数据类

    需求: 9.1 - 查询系统指标时返回 Stream 长度、PEL 大小、去重集合大小等
    """

    project_id: str = ""

    # 队列指标
    total_stream_length: int = 0
    total_pel_size: int = 0
    dead_letter_count: int = 0

    # 去重指标
    dedup_size: int = 0

    # Worker 指标
    active_workers: int = 0
    total_consumers: int = 0

    # 各优先级队列指标
    queues: dict = field(default_factory=dict)

    # 时间戳
    collected_at: str = ""

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "project_id": self.project_id,
            "total_stream_length": self.total_stream_length,
            "total_pel_size": self.total_pel_size,
            "dead_letter_count": self.dead_letter_count,
            "dedup_size": self.dedup_size,
            "active_workers": self.active_workers,
            "total_consumers": self.total_consumers,
            "queues": self.queues,
            "collected_at": self.collected_at,
        }


@dataclass
class BatchMetrics:
    """批次指标数据类

    需求: 9.2 - 查询批次指标时返回完成数、失败数、速度、活跃 Worker 数等
    """

    batch_id: str = ""
    project_id: str = ""
    status: str = ""

    # 进度指标
    total_urls: int = 0
    completed_urls: int = 0
    failed_urls: int = 0
    pending_urls: int = 0

    # 速度指标
    speed_per_minute: float = 0.0

    # Worker 指标
    active_workers: int = 0

    # 队列指标
    queue_length: int = 0
    pel_size: int = 0

    # 配置信息
    max_depth: int = 0
    max_pages: int = 0
    max_concurrency: int = 0
    max_retries: int = 0

    # 时间戳
    collected_at: str = ""

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "status": self.status,
            "progress": {
                "total_urls": self.total_urls,
                "completed_urls": self.completed_urls,
                "failed_urls": self.failed_urls,
                "pending_urls": self.pending_urls,
                "speed_per_minute": self.speed_per_minute,
                "active_workers": self.active_workers,
            },
            "queue": {
                "length": self.queue_length,
                "pel_size": self.pel_size,
            },
            "config": {
                "max_depth": self.max_depth,
                "max_pages": self.max_pages,
                "max_concurrency": self.max_concurrency,
                "max_retries": self.max_retries,
            },
            "collected_at": self.collected_at,
        }


@dataclass
class AlertConfig:
    """告警配置"""

    stream_length_threshold: int = DEFAULT_STREAM_LENGTH_THRESHOLD
    pel_size_threshold: int = DEFAULT_PEL_SIZE_THRESHOLD
    dead_letter_threshold: int = DEFAULT_DEAD_LETTER_THRESHOLD
    dedup_size_threshold: int = DEFAULT_DEDUP_SIZE_THRESHOLD


@dataclass
class Alert:
    """告警信息"""

    level: str = "warning"  # warning, critical
    metric_name: str = ""
    current_value: int = 0
    threshold: int = 0
    message: str = ""
    project_id: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "level": self.level,
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "message": self.message,
            "project_id": self.project_id,
            "created_at": self.created_at,
        }


class CrawlMetricsService(BaseService):
    """监控指标收集服务

    收集和管理爬取系统的监控指标，支持：
    - Stream 长度、PEL 大小、去重集合大小等指标收集
    - 批次级别的进度和性能指标
    - 阈值检测和告警日志记录

    需求: 9.1, 9.2, 9.3
    """

    def __init__(
        self,
        backend=None,
        dedup_store: DedupStore = None,
        alert_config: AlertConfig = None,
    ):
        """初始化监控服务

        Args:
            backend: 队列后端
            dedup_store: 去重存储后端
            alert_config: 告警配置
        """
        super().__init__()
        self._backend = backend
        self._dedup_store = dedup_store
        self._alert_config = alert_config or AlertConfig()

    @property
    def backend(self):
        """获取队列后端（延迟初始化）"""
        if self._backend is None:
            self._backend = get_queue_backend()
        return self._backend

    @property
    def dedup_store(self) -> DedupStore:
        """获取去重存储后端（延迟初始化）"""
        if self._dedup_store is None:
            self._dedup_store = get_dedup_store()
        return self._dedup_store

    # =========================================================================
    # 系统指标收集
    # =========================================================================

    async def collect_system_metrics(self, project_id: str) -> SystemMetrics:
        """收集系统级监控指标

        Args:
            project_id: 项目 ID

        Returns:
            SystemMetrics 对象

        需求: 9.1 - 查询系统指标时返回 Stream 长度、PEL 大小、去重集合大小等
        """
        now = datetime.now().isoformat()

        metrics = SystemMetrics(
            project_id=project_id,
            collected_at=now,
        )

        # 收集各优先级队列指标
        all_consumers = set()

        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            queue_metrics = await self._collect_queue_metrics(project_id, priority)

            priority_name = self._get_priority_name(priority)
            metrics.queues[priority_name] = {
                "priority": priority,
                "stream_length": queue_metrics.stream_length,
                "pending_count": queue_metrics.pending_count,
                "consumers": queue_metrics.consumers,
            }

            metrics.total_stream_length += queue_metrics.stream_length
            metrics.total_pel_size += queue_metrics.pending_count

            # 收集消费者
            all_consumers.update(queue_metrics.consumers.keys())

        # 收集死信队列指标
        metrics.dead_letter_count = await self._get_dead_letter_count(project_id)

        # 收集去重集合大小
        metrics.dedup_size = await self._get_dedup_size(project_id)

        # 统计消费者数量
        metrics.total_consumers = len(all_consumers)
        metrics.active_workers = len(all_consumers)

        logger.debug(
            f"收集系统指标: project={project_id}, "
            f"stream_length={metrics.total_stream_length}, "
            f"pel_size={metrics.total_pel_size}, "
            f"dedup_size={metrics.dedup_size}"
        )

        return metrics

    async def _collect_queue_metrics(
        self,
        project_id: str,
        priority: int,
    ) -> QueueMetrics:
        """收集单个队列的指标

        Args:
            project_id: 项目 ID
            priority: 优先级

        Returns:
            QueueMetrics 对象
        """
        queue_metrics = await self.backend.get_queue_metrics(project_id, priority)
        return QueueMetrics(
            priority=priority,
            priority_name=self._get_priority_name(priority),
            stream_length=queue_metrics.queue_length,
            pending_count=queue_metrics.pending_count,
            consumers=queue_metrics.consumers,
        )

    async def _get_dead_letter_count(self, project_id: str) -> int:
        """获取死信队列消息数量

        Args:
            project_id: 项目 ID

        Returns:
            消息数量
        """
        return await self.backend.get_dead_letter_count(project_id)

    async def _get_dedup_size(self, project_id: str) -> int:
        """获取去重集合大小

        Args:
            project_id: 项目 ID

        Returns:
            集合大小
        """
        return await self.dedup_store.size(project_id)

    def _get_priority_name(self, priority: int) -> str:
        """获取优先级名称"""
        return {
            Priority.HIGH: "high",
            Priority.NORMAL: "normal",
            Priority.LOW: "low",
        }.get(priority, str(priority))

    # =========================================================================
    # 批次指标收集
    # =========================================================================

    async def collect_batch_metrics(
        self,
        project_id: str,
        batch_id: str,
        batch_status: str = "",
        progress: dict = None,
        config: dict = None,
    ) -> BatchMetrics:
        """收集批次级监控指标

        Args:
            project_id: 项目 ID
            batch_id: 批次 ID
            batch_status: 批次状态
            progress: 进度信息
            config: 配置信息

        Returns:
            BatchMetrics 对象

        需求: 9.2 - 查询批次指标时返回完成数、失败数、速度、活跃 Worker 数等
        """
        now = datetime.now().isoformat()
        progress = progress or {}
        config = config or {}

        # 收集队列指标
        system_metrics = await self.collect_system_metrics(project_id)

        metrics = BatchMetrics(
            batch_id=batch_id,
            project_id=project_id,
            status=batch_status,
            total_urls=progress.get("total_urls", 0),
            completed_urls=progress.get("completed_urls", 0),
            failed_urls=progress.get("failed_urls", 0),
            pending_urls=progress.get("pending_urls", 0),
            speed_per_minute=progress.get("speed_per_minute", 0.0),
            active_workers=progress.get("active_workers", 0),
            queue_length=system_metrics.total_stream_length,
            pel_size=system_metrics.total_pel_size,
            max_depth=config.get("max_depth", 0),
            max_pages=config.get("max_pages", 0),
            max_concurrency=config.get("max_concurrency", 0),
            max_retries=config.get("max_retries", 0),
            collected_at=now,
        )

        logger.debug(
            f"收集批次指标: project={project_id}, batch={batch_id}, "
            f"completed={metrics.completed_urls}, failed={metrics.failed_urls}"
        )

        return metrics

    # =========================================================================
    # 告警检测
    # =========================================================================

    async def check_alerts(self, project_id: str) -> list:
        """检测告警

        检查各项指标是否超过阈值，生成告警信息。

        Args:
            project_id: 项目 ID

        Returns:
            Alert 列表

        需求: 9.3 - 指标超过阈值时记录告警日志
        """
        alerts = []
        now = datetime.now().isoformat()

        # 收集当前指标
        metrics = await self.collect_system_metrics(project_id)

        # 检查 Stream 长度
        if metrics.total_stream_length > self._alert_config.stream_length_threshold:
            alert = Alert(
                level="warning",
                metric_name="stream_length",
                current_value=metrics.total_stream_length,
                threshold=self._alert_config.stream_length_threshold,
                message=f"Stream 队列长度 ({metrics.total_stream_length}) "
                f"超过阈值 ({self._alert_config.stream_length_threshold})",
                project_id=project_id,
                created_at=now,
            )
            alerts.append(alert)
            self._log_alert(alert)

        # 检查 PEL 大小
        if metrics.total_pel_size > self._alert_config.pel_size_threshold:
            alert = Alert(
                level="warning",
                metric_name="pel_size",
                current_value=metrics.total_pel_size,
                threshold=self._alert_config.pel_size_threshold,
                message=f"PEL 待确认消息数 ({metrics.total_pel_size}) "
                f"超过阈值 ({self._alert_config.pel_size_threshold})",
                project_id=project_id,
                created_at=now,
            )
            alerts.append(alert)
            self._log_alert(alert)

        # 检查死信队列
        if metrics.dead_letter_count > self._alert_config.dead_letter_threshold:
            alert = Alert(
                level="critical",
                metric_name="dead_letter_count",
                current_value=metrics.dead_letter_count,
                threshold=self._alert_config.dead_letter_threshold,
                message=f"死信队列消息数 ({metrics.dead_letter_count}) "
                f"超过阈值 ({self._alert_config.dead_letter_threshold})",
                project_id=project_id,
                created_at=now,
            )
            alerts.append(alert)
            self._log_alert(alert)

        # 检查去重集合大小
        if metrics.dedup_size > self._alert_config.dedup_size_threshold:
            alert = Alert(
                level="warning",
                metric_name="dedup_size",
                current_value=metrics.dedup_size,
                threshold=self._alert_config.dedup_size_threshold,
                message=f"去重集合大小 ({metrics.dedup_size}) "
                f"超过阈值 ({self._alert_config.dedup_size_threshold})",
                project_id=project_id,
                created_at=now,
            )
            alerts.append(alert)
            self._log_alert(alert)

        return alerts

    def _log_alert(self, alert: Alert):
        """记录告警日志

        Args:
            alert: 告警信息

        需求: 9.3 - 指标超过阈值时记录告警日志
        """
        if alert.level == "critical":
            logger.error(
                f"[告警-严重] {alert.message} | "
                f"project={alert.project_id}, "
                f"metric={alert.metric_name}, "
                f"value={alert.current_value}, "
                f"threshold={alert.threshold}"
            )
        else:
            logger.warning(
                f"[告警-警告] {alert.message} | "
                f"project={alert.project_id}, "
                f"metric={alert.metric_name}, "
                f"value={alert.current_value}, "
                f"threshold={alert.threshold}"
            )

    # =========================================================================
    # 告警配置管理
    # =========================================================================

    def update_alert_config(
        self,
        stream_length_threshold: int = None,
        pel_size_threshold: int = None,
        dead_letter_threshold: int = None,
        dedup_size_threshold: int = None,
    ):
        """更新告警配置

        Args:
            stream_length_threshold: Stream 长度告警阈值
            pel_size_threshold: PEL 大小告警阈值
            dead_letter_threshold: 死信队列告警阈值
            dedup_size_threshold: 去重集合大小告警阈值
        """
        if stream_length_threshold is not None:
            self._alert_config.stream_length_threshold = stream_length_threshold
        if pel_size_threshold is not None:
            self._alert_config.pel_size_threshold = pel_size_threshold
        if dead_letter_threshold is not None:
            self._alert_config.dead_letter_threshold = dead_letter_threshold
        if dedup_size_threshold is not None:
            self._alert_config.dedup_size_threshold = dedup_size_threshold

        logger.info(
            f"更新告警配置: stream_length={self._alert_config.stream_length_threshold}, "
            f"pel_size={self._alert_config.pel_size_threshold}, "
            f"dead_letter={self._alert_config.dead_letter_threshold}, "
            f"dedup_size={self._alert_config.dedup_size_threshold}"
        )

    def get_alert_config(self) -> dict:
        """获取当前告警配置

        Returns:
            告警配置字典
        """
        return {
            "stream_length_threshold": self._alert_config.stream_length_threshold,
            "pel_size_threshold": self._alert_config.pel_size_threshold,
            "dead_letter_threshold": self._alert_config.dead_letter_threshold,
            "dedup_size_threshold": self._alert_config.dedup_size_threshold,
        }

    # =========================================================================
    # 指标汇总
    # =========================================================================

    async def get_metrics_summary(self, project_id: str) -> dict:
        """获取指标汇总

        Args:
            project_id: 项目 ID

        Returns:
            指标汇总字典
        """
        metrics = await self.collect_system_metrics(project_id)
        alerts = await self.check_alerts(project_id)

        return {
            "metrics": metrics.to_dict(),
            "alerts": [a.to_dict() for a in alerts],
            "alert_count": len(alerts),
            "has_critical_alerts": any(a.level == "critical" for a in alerts),
        }


# 全局服务实例
crawl_metrics_service = CrawlMetricsService()


def create_metrics_service(
    backend=None,
    dedup_store: DedupStore = None,
    alert_config: AlertConfig = None,
) -> CrawlMetricsService:
    """创建监控指标服务实例

    Args:
        backend: 队列后端
        dedup_store: 去重存储后端
        alert_config: 告警配置

    Returns:
        CrawlMetricsService 实例
    """
    return CrawlMetricsService(
        backend=backend,
        dedup_store=dedup_store,
        alert_config=alert_config,
    )
