"""
配额服务

提供资源配额检查和管理。
"""

from dataclasses import dataclass


@dataclass
class QuotaLimit:
    """配额限制"""
    max_projects: int = 100
    max_tasks_per_project: int = 50
    max_concurrent_tasks: int = 10
    max_runtimes: int = 20
    max_storage_mb: int = 10240  # 10GB
    max_log_retention_days: int = 30


@dataclass
class QuotaUsage:
    """配额使用情况"""
    projects: int = 0
    tasks: int = 0
    concurrent_tasks: int = 0
    runtimes: int = 0
    storage_mb: float = 0.0


class QuotaService:
    """配额服务

    提供资源配额检查和管理功能。
    """

    def __init__(self, limit: QuotaLimit | None = None):
        self.limit = limit or QuotaLimit()

    def check_project_quota(self, current_count: int) -> bool:
        """检查项目配额"""
        return current_count < self.limit.max_projects

    def check_task_quota(self, current_count: int) -> bool:
        """检查任务配额"""
        return current_count < self.limit.max_tasks_per_project

    def check_concurrent_task_quota(self, current_count: int) -> bool:
        """检查并发任务配额"""
        return current_count < self.limit.max_concurrent_tasks

    def check_runtime_quota(self, current_count: int) -> bool:
        """检查运行时环境配额"""
        return current_count < self.limit.max_runtimes

    def check_storage_quota(self, current_mb: float, additional_mb: float) -> bool:
        """检查存储配额"""
        return (current_mb + additional_mb) <= self.limit.max_storage_mb

    def get_remaining_quota(self, usage: QuotaUsage) -> dict:
        """获取剩余配额"""
        return {
            "projects": max(0, self.limit.max_projects - usage.projects),
            "tasks": max(0, self.limit.max_tasks_per_project - usage.tasks),
            "concurrent_tasks": max(0, self.limit.max_concurrent_tasks - usage.concurrent_tasks),
            "runtimes": max(0, self.limit.max_runtimes - usage.runtimes),
            "storage_mb": max(0, self.limit.max_storage_mb - usage.storage_mb),
        }


__all__ = [
    "QuotaLimit",
    "QuotaUsage",
    "QuotaService",
]
