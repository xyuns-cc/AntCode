"""
调度策略

实现任务调度的各种策略：
- 优先级调度
- 配额管理
- 任务老化（防止饿死）
- 负载均衡
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class TaskPriority:
    """任务优先级"""
    HIGH = 0
    NORMAL = 5
    LOW = 9


@dataclass
class WorkerQuota:
    """Worker 配额"""
    worker_id: int
    max_concurrent: int
    current_running: int

    @property
    def available(self) -> int:
        """可用配额"""
        return max(0, self.max_concurrent - self.current_running)

    @property
    def is_full(self) -> bool:
        """是否已满"""
        return self.current_running >= self.max_concurrent


class PriorityPolicy:
    """优先级调度策略"""

    @staticmethod
    def calculate_priority(task, age_seconds: int = 0) -> int:
        """计算任务优先级

        Args:
            task: 任务对象
            age_seconds: 任务等待时间（秒）

        Returns:
            优先级值（越小越高）
        """
        base_priority = getattr(task, 'priority', TaskPriority.NORMAL)

        # 任务老化：等待时间越长，优先级越高
        age_bonus = min(age_seconds // 60, 5)  # 每分钟提升1，最多提升5

        return max(0, base_priority - age_bonus)

    @staticmethod
    def sort_tasks_by_priority(tasks: list, current_time: datetime = None) -> list:
        """按优先级排序任务

        Args:
            tasks: 任务列表
            current_time: 当前时间

        Returns:
            排序后的任务列表
        """
        if current_time is None:
            current_time = datetime.now()

        def get_priority(task):
            created_at = getattr(task, 'created_at', current_time)
            age_seconds = int((current_time - created_at).total_seconds())
            return PriorityPolicy.calculate_priority(task, age_seconds)

        return sorted(tasks, key=get_priority)


class QuotaPolicy:
    """配额管理策略"""

    def __init__(self):
        self._quotas: dict[int, WorkerQuota] = {}

    async def update_worker_quota(self, worker_id: int, max_concurrent: int, current_running: int):
        """更新 Worker 配额

        Args:
            worker_id: Worker ID
            max_concurrent: 最大并发数
            current_running: 当前运行数
        """
        self._quotas[worker_id] = WorkerQuota(
            worker_id=worker_id,
            max_concurrent=max_concurrent,
            current_running=current_running,
        )

    def get_available_workers(self) -> list[int]:
        """获取有可用配额的 Worker

        Returns:
            Worker ID 列表
        """
        return [
            worker_id
            for worker_id, quota in self._quotas.items()
            if not quota.is_full
        ]

    def get_worker_quota(self, worker_id: int) -> WorkerQuota | None:
        """获取 Worker 配额

        Args:
            worker_id: Worker ID

        Returns:
            Worker 配额
        """
        return self._quotas.get(worker_id)

    def can_dispatch(self, worker_id: int) -> bool:
        """检查是否可以向 Worker 分发任务

        Args:
            worker_id: Worker ID

        Returns:
            是否可以分发
        """
        quota = self._quotas.get(worker_id)
        if quota is None:
            return False
        return not quota.is_full


class AntiStarvationPolicy:
    """防饿死策略"""

    def __init__(self, starvation_threshold: int = 300):
        """初始化防饿死策略

        Args:
            starvation_threshold: 饿死阈值（秒）
        """
        self.starvation_threshold = starvation_threshold

    def detect_starving_tasks(self, tasks: list, current_time: datetime = None) -> list:
        """检测饿死任务

        Args:
            tasks: 任务列表
            current_time: 当前时间

        Returns:
            饿死任务列表
        """
        if current_time is None:
            current_time = datetime.now()

        starving_tasks = []
        threshold_time = current_time - timedelta(seconds=self.starvation_threshold)

        for task in tasks:
            created_at = getattr(task, 'created_at', current_time)
            if created_at < threshold_time:
                starving_tasks.append(task)

        return starving_tasks

    def boost_priority(self, task) -> int:
        """提升饿死任务的优先级

        Args:
            task: 任务对象

        Returns:
            提升后的优先级
        """
        return TaskPriority.HIGH


class LoadBalancePolicy:
    """负载均衡策略"""

    @staticmethod
    def select_least_loaded_worker(quotas: list[WorkerQuota]) -> int | None:
        """选择负载最低的 Worker

        Args:
            quotas: Worker 配额列表

        Returns:
            Worker ID
        """
        available_quotas = [q for q in quotas if not q.is_full]
        if not available_quotas:
            return None

        # 选择当前运行任务最少的 Worker
        least_loaded = min(available_quotas, key=lambda q: q.current_running)
        return least_loaded.worker_id

    @staticmethod
    def select_most_available_worker(quotas: list[WorkerQuota]) -> int | None:
        """选择可用配额最多的 Worker

        Args:
            quotas: Worker 配额列表

        Returns:
            Worker ID
        """
        available_quotas = [q for q in quotas if not q.is_full]
        if not available_quotas:
            return None

        # 选择可用配额最多的 Worker
        most_available = max(available_quotas, key=lambda q: q.available)
        return most_available.worker_id


# 全局策略实例
priority_policy = PriorityPolicy()
quota_policy = QuotaPolicy()
anti_starvation_policy = AntiStarvationPolicy()
load_balance_policy = LoadBalancePolicy()
