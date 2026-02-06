"""内存进度存储实现

基于内存字典实现进度存储，支持 Worker TTL 过期检测。

Requirements: 3.3, 3.4, 3.5, 3.6, 3.7
"""

import time
from typing import Any

from antcode_core.application.services.crawl.backends.progress_backend import ProgressStore


class InMemoryProgressStore(ProgressStore):
    """内存进度存储实现

    使用内存字典存储进度数据，支持：
    - 进度数据的获取和设置
    - Worker 活跃状态注册和 TTL 过期检测
    - 检查点保存和加载

    适用于单机开发和测试环境。

    Requirements: 3.3, 3.4, 3.5, 3.6, 3.7
    """

    def __init__(self, default_worker_ttl: int = 60):
        """初始化内存进度存储

        Args:
            default_worker_ttl: 默认 Worker TTL（秒）
        """
        self._default_worker_ttl = default_worker_ttl
        # 进度数据: {project_id:batch_id: {field: value}}
        self._progress: dict[str, dict[str, Any]] = {}
        # Worker 注册: {project_id:batch_id: {worker_id: (timestamp, ttl)}}
        self._workers: dict[str, dict[str, tuple[float, int]]] = {}
        # 检查点: {project_id:batch_id: checkpoint_data}
        self._checkpoints: dict[str, dict[str, Any]] = {}

    def _get_key(self, project_id: str, batch_id: str) -> str:
        """生成存储键"""
        return f"{project_id}:{batch_id}"

    async def get_progress(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        """获取批次进度"""
        key = self._get_key(project_id, batch_id)
        data = self._progress.get(key)
        if data is None:
            return None
        # 返回副本避免外部修改
        return dict(data)

    async def set_progress(
        self,
        project_id: str,
        batch_id: str,
        data: dict[str, Any],
    ) -> bool:
        """设置批次进度"""
        key = self._get_key(project_id, batch_id)
        self._progress[key] = dict(data)
        return True

    async def update_progress(
        self,
        project_id: str,
        batch_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """增量更新批次进度"""
        key = self._get_key(project_id, batch_id)
        if key not in self._progress:
            self._progress[key] = {}
        self._progress[key].update(updates)
        return True

    async def increment_progress(
        self,
        project_id: str,
        batch_id: str,
        field: str,
        amount: int = 1,
    ) -> int:
        """原子增加进度字段值"""
        key = self._get_key(project_id, batch_id)
        if key not in self._progress:
            self._progress[key] = {}

        current = self._progress[key].get(field, 0)
        if not isinstance(current, (int, float)):
            current = 0

        new_value = int(current) + amount
        self._progress[key][field] = new_value
        return new_value

    async def register_worker(
        self,
        project_id: str,
        batch_id: str,
        worker_id: str,
        ttl: int = 60,
    ) -> bool:
        """注册活跃 Worker"""
        key = self._get_key(project_id, batch_id)
        if key not in self._workers:
            self._workers[key] = {}

        self._workers[key][worker_id] = (time.time(), ttl)
        return True

    async def get_active_workers(
        self,
        project_id: str,
        batch_id: str,
    ) -> list[str]:
        """获取活跃 Worker 列表"""
        key = self._get_key(project_id, batch_id)
        workers = self._workers.get(key, {})

        if not workers:
            return []

        now = time.time()
        active = []
        expired = []

        for worker_id, (timestamp, ttl) in workers.items():
            if now - timestamp < ttl:
                active.append(worker_id)
            else:
                expired.append(worker_id)

        # 清理过期 Worker
        for worker_id in expired:
            del self._workers[key][worker_id]

        return active

    async def unregister_worker(
        self,
        project_id: str,
        batch_id: str,
        worker_id: str,
    ) -> bool:
        """注销 Worker"""
        key = self._get_key(project_id, batch_id)
        workers = self._workers.get(key, {})

        if worker_id in workers:
            del workers[worker_id]
            return True
        return False

    async def save_checkpoint(
        self,
        project_id: str,
        batch_id: str,
        checkpoint_data: dict[str, Any],
    ) -> bool:
        """保存检查点"""
        key = self._get_key(project_id, batch_id)
        self._checkpoints[key] = dict(checkpoint_data)
        return True

    async def load_checkpoint(
        self,
        project_id: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        """加载检查点"""
        key = self._get_key(project_id, batch_id)
        data = self._checkpoints.get(key)
        if data is None:
            return None
        return dict(data)

    async def delete_checkpoint(
        self,
        project_id: str,
        batch_id: str,
    ) -> bool:
        """删除检查点"""
        key = self._get_key(project_id, batch_id)
        if key in self._checkpoints:
            del self._checkpoints[key]
            return True
        return False

    async def clear(
        self,
        project_id: str,
        batch_id: str,
    ) -> bool:
        """清除批次所有进度数据"""
        key = self._get_key(project_id, batch_id)

        self._progress.pop(key, None)
        self._workers.pop(key, None)
        self._checkpoints.pop(key, None)

        return True

    def clear_all(self) -> None:
        """清除所有数据（用于测试）"""
        self._progress.clear()
        self._workers.clear()
        self._checkpoints.clear()
