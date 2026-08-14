"""Checkpoint、worker 与批次进度清理生命周期。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime

from loguru import logger

from antcode_core.application.services.crawl.backends.progress_backend import ProgressStore
from antcode_core.application.services.crawl.progress_models import BatchProgress, Checkpoint


class CrawlProgressLifecycleMixin(ABC):
    """为进度服务提供持久化生命周期操作。"""

    _checkpoint_interval: int
    _worker_timeout: int
    _speed_history: dict[str, list[tuple[float, int]]]
    _last_checkpoint_time: dict[str, float]

    @abstractmethod
    def _get_backend(self) -> ProgressStore:
        """返回当前进度后端。"""

    @abstractmethod
    async def get_progress(self, project_id: str, batch_id: str) -> BatchProgress | None:
        """读取批次进度。"""

    @staticmethod
    @abstractmethod
    def _require_active_write(updated: bool, *, operation: str) -> None:
        """校验写操作未被取消 fence 拒绝。"""

    async def save_checkpoint(
        self,
        project_id: str,
        batch_id: str,
        queue_state: dict | None = None,
    ) -> Checkpoint:
        backend = self._get_backend()
        progress = await self.get_progress(project_id, batch_id)
        checkpoint = Checkpoint(
            batch_id=batch_id,
            project_id=project_id,
            progress=progress.to_dict() if progress else {},
            queue_state=queue_state or {},
            created_at=datetime.now().isoformat(),
        )
        self._require_active_write(
            await backend.save_checkpoint(project_id, batch_id, checkpoint.to_dict()),
            operation="保存检查点",
        )
        self._last_checkpoint_time[self._batch_key(project_id, batch_id)] = time.time()
        logger.info(f"保存检查点: project={project_id}, batch={batch_id}")
        return checkpoint

    async def load_checkpoint(self, project_id: str, batch_id: str) -> Checkpoint | None:
        data = await self._get_backend().load_checkpoint(project_id, batch_id)
        if not data:
            return None
        logger.info(f"加载检查点: project={project_id}, batch={batch_id}")
        return Checkpoint.from_dict(data)

    async def restore_from_checkpoint(self, project_id: str, batch_id: str) -> BatchProgress | None:
        checkpoint = await self.load_checkpoint(project_id, batch_id)
        if not checkpoint:
            return None
        if checkpoint.progress:
            self._require_active_write(
                await self._get_backend().set_progress(project_id, batch_id, checkpoint.progress),
                operation="恢复检查点",
            )
        logger.info(f"从检查点恢复进度: project={project_id}, batch={batch_id}")
        return await self.get_progress(project_id, batch_id)

    async def _maybe_save_checkpoint(
        self,
        project_id: str,
        batch_id: str,
        _progress: BatchProgress,
    ) -> None:
        batch_key = self._batch_key(project_id, batch_id)
        last_time = self._last_checkpoint_time.get(batch_key, 0)
        if time.time() - last_time >= self._checkpoint_interval:
            await self.save_checkpoint(project_id, batch_id)

    async def delete_checkpoint(self, project_id: str, batch_id: str) -> bool:
        result = await self._get_backend().delete_checkpoint(project_id, batch_id)
        logger.info(f"删除检查点: project={project_id}, batch={batch_id}")
        return result

    async def register_worker(self, project_id: str, batch_id: str, worker_id: str) -> bool:
        result = await self._get_backend().register_worker(
            project_id,
            batch_id,
            worker_id,
            self._worker_timeout,
        )
        logger.debug(f"注册 Worker: project={project_id}, batch={batch_id}, worker={worker_id}")
        return result

    async def update_worker_heartbeat(self, project_id: str, batch_id: str, worker_id: str) -> bool:
        return await self.register_worker(project_id, batch_id, worker_id)

    async def get_active_worker_count(self, project_id: str, batch_id: str) -> int:
        return len(await self._get_backend().get_active_workers(project_id, batch_id))

    async def get_active_workers(self, project_id: str, batch_id: str) -> list:
        return await self._get_backend().get_active_workers(project_id, batch_id)

    async def unregister_worker(self, project_id: str, batch_id: str, worker_id: str) -> bool:
        result = await self._get_backend().unregister_worker(project_id, batch_id, worker_id)
        logger.debug(f"注销 Worker: project={project_id}, batch={batch_id}, worker={worker_id}")
        return result

    async def clear_progress(self, project_id: str, batch_id: str) -> bool:
        result = await self._get_backend().clear(project_id, batch_id)
        self._clear_runtime_state(project_id, batch_id)
        logger.info(f"清除批次进度数据: project={project_id}, batch={batch_id}")
        return result

    async def cancel_progress(self, project_id: str, batch_id: str) -> bool:
        """设置取消 fence 后原子清理，拒绝状态 loop 的迟到重建。"""
        result = await self._get_backend().fence_and_clear(project_id, batch_id)
        self._clear_runtime_state(project_id, batch_id)
        return result

    def _clear_runtime_state(self, project_id: str, batch_id: str) -> None:
        batch_key = self._batch_key(project_id, batch_id)
        self._speed_history.pop(batch_key, None)
        self._last_checkpoint_time.pop(batch_key, None)

    @staticmethod
    def _batch_key(project_id: str, batch_id: str) -> str:
        return f"{project_id}:{batch_id}"
