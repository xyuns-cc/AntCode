"""批次进度服务

基于抽象后端实现批次进度管理，支持：
- 进度更新和查询
- 检查点保存和加载
- 速度计算

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

import time
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from antcode_core.application.services.base import BaseService
from antcode_core.application.services.crawl.backends.progress_backend import (
    ProgressStore,
    get_progress_store,
)

DEFAULT_CHECKPOINT_INTERVAL = 60
DEFAULT_SPEED_WINDOW = 60
DEFAULT_WORKER_TIMEOUT = 120


@dataclass
class BatchProgress:
    """批次进度数据类"""
    batch_id: str = ""
    project_id: str = ""
    total_urls: int = 0
    pending_urls: int = 0
    completed_urls: int = 0
    failed_urls: int = 0
    active_workers: int = 0
    speed_per_minute: float = 0.0
    last_updated: str = ""
    started_at: str = ""
    _completed_history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "total_urls": self.total_urls,
            "pending_urls": self.pending_urls,
            "completed_urls": self.completed_urls,
            "failed_urls": self.failed_urls,
            "active_workers": self.active_workers,
            "speed_per_minute": self.speed_per_minute,
            "last_updated": self.last_updated,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BatchProgress":
        return cls(
            batch_id=data.get("batch_id", ""),
            project_id=data.get("project_id", ""),
            total_urls=int(data.get("total_urls", 0)),
            pending_urls=int(data.get("pending_urls", 0)),
            completed_urls=int(data.get("completed_urls", 0)),
            failed_urls=int(data.get("failed_urls", 0)),
            active_workers=int(data.get("active_workers", 0)),
            speed_per_minute=float(data.get("speed_per_minute", 0.0)),
            last_updated=data.get("last_updated", ""),
            started_at=data.get("started_at", ""),
        )



@dataclass
class Checkpoint:
    """检查点数据类"""
    batch_id: str = ""
    project_id: str = ""
    progress: dict = field(default_factory=dict)
    queue_state: dict = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "progress": self.progress,
            "queue_state": self.queue_state,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        return cls(
            batch_id=data.get("batch_id", ""),
            project_id=data.get("project_id", ""),
            progress=data.get("progress", {}),
            queue_state=data.get("queue_state", {}),
            created_at=data.get("created_at", ""),
        )


class CrawlProgressService(BaseService):
    """批次进度服务"""

    def __init__(
        self,
        backend: ProgressStore | None = None,
        checkpoint_interval: int = None,
        speed_window: int = None,
        worker_timeout: int = None,
    ):
        super().__init__()
        self._backend = backend
        self._checkpoint_interval = checkpoint_interval or DEFAULT_CHECKPOINT_INTERVAL
        self._speed_window = speed_window or DEFAULT_SPEED_WINDOW
        self._worker_timeout = worker_timeout or DEFAULT_WORKER_TIMEOUT
        self._speed_history: dict[str, list[tuple[float, int]]] = {}
        self._last_checkpoint_time: dict[str, float] = {}

    def _get_backend(self) -> ProgressStore:
        if self._backend is None:
            self._backend = get_progress_store()
        return self._backend

    async def init_progress(
        self,
        project_id: str,
        batch_id: str,
        total_urls: int = 0,
    ) -> BatchProgress:
        backend = self._get_backend()
        now = datetime.now().isoformat()
        progress = BatchProgress(
            batch_id=batch_id,
            project_id=project_id,
            total_urls=total_urls,
            pending_urls=total_urls,
            completed_urls=0,
            failed_urls=0,
            active_workers=0,
            speed_per_minute=0.0,
            last_updated=now,
            started_at=now,
        )
        await backend.set_progress(project_id, batch_id, progress.to_dict())
        logger.info(f"初始化批次进度: project={project_id}, batch={batch_id}, total_urls={total_urls}")
        return progress

    async def update_progress(
        self,
        project_id: str,
        batch_id: str,
        completed: int = 0,
        failed: int = 0,
        new_urls: int = 0,
    ) -> BatchProgress:
        backend = self._get_backend()
        current = await backend.get_progress(project_id, batch_id)
        if current is None:
            current = {}
        total = int(current.get("total_urls", 0)) + new_urls
        pending = int(current.get("pending_urls", 0)) - completed - failed + new_urls
        curr_completed = int(current.get("completed_urls", 0)) + completed
        curr_failed = int(current.get("failed_urls", 0)) + failed
        if pending < 0:
            pending = 0
        now = datetime.now().isoformat()
        batch_key = f"{project_id}:{batch_id}"
        await self._update_speed_history(batch_key, curr_completed)
        speed = await self._calculate_speed(batch_key)
        active_workers = len(await backend.get_active_workers(project_id, batch_id))
        updates = {
            "total_urls": total,
            "pending_urls": pending,
            "completed_urls": curr_completed,
            "failed_urls": curr_failed,
            "speed_per_minute": speed,
            "last_updated": now,
        }
        await backend.update_progress(project_id, batch_id, updates)
        progress = BatchProgress(
            batch_id=batch_id,
            project_id=project_id,
            total_urls=total,
            pending_urls=pending,
            completed_urls=curr_completed,
            failed_urls=curr_failed,
            active_workers=active_workers,
            speed_per_minute=speed,
            last_updated=now,
            started_at=current.get("started_at", ""),
        )
        logger.debug(f"更新批次进度: project={project_id}, batch={batch_id}, completed={completed}, failed={failed}, new_urls={new_urls}")
        await self._maybe_save_checkpoint(project_id, batch_id, progress)
        return progress

    async def increment_total_urls(self, project_id: str, batch_id: str, count: int) -> int:
        backend = self._get_backend()
        new_total = await backend.increment_progress(project_id, batch_id, "total_urls", count)
        await backend.increment_progress(project_id, batch_id, "pending_urls", count)
        await backend.update_progress(project_id, batch_id, {"last_updated": datetime.now().isoformat()})
        logger.debug(f"增加 URL 数: project={project_id}, batch={batch_id}, count={count}, new_total={new_total}")
        return new_total

    async def _update_speed_history(self, batch_key: str, completed_count: int):
        now = time.time()
        if batch_key not in self._speed_history:
            self._speed_history[batch_key] = []
        history = self._speed_history[batch_key]
        history.append((now, completed_count))
        cutoff = now - self._speed_window
        self._speed_history[batch_key] = [(ts, count) for ts, count in history if ts > cutoff]

    async def _calculate_speed(self, batch_key: str) -> float:
        if batch_key not in self._speed_history:
            return 0.0
        history = self._speed_history[batch_key]
        if len(history) < 2:
            return 0.0
        oldest = history[0]
        newest = history[-1]
        time_diff = newest[0] - oldest[0]
        if time_diff <= 0:
            return 0.0
        count_diff = newest[1] - oldest[1]
        speed = (count_diff / time_diff) * 60
        return round(speed, 2)


    async def get_progress(self, project_id: str, batch_id: str) -> BatchProgress | None:
        backend = self._get_backend()
        data = await backend.get_progress(project_id, batch_id)
        if not data:
            return None
        active_workers = len(await backend.get_active_workers(project_id, batch_id))
        data["active_workers"] = active_workers
        return BatchProgress.from_dict(data)

    async def get_progress_summary(self, project_id: str, batch_id: str) -> dict:
        progress = await self.get_progress(project_id, batch_id)
        if not progress:
            return {"batch_id": batch_id, "project_id": project_id, "status": "not_found"}
        total = progress.total_urls
        completed = progress.completed_urls
        percentage = (completed / total * 100) if total > 0 else 0
        return {
            "batch_id": batch_id,
            "project_id": project_id,
            "total_urls": total,
            "completed_urls": completed,
            "failed_urls": progress.failed_urls,
            "pending_urls": progress.pending_urls,
            "percentage": round(percentage, 2),
            "speed_per_minute": progress.speed_per_minute,
            "active_workers": progress.active_workers,
            "last_updated": progress.last_updated,
        }

    async def save_checkpoint(self, project_id: str, batch_id: str, queue_state: dict = None) -> Checkpoint:
        backend = self._get_backend()
        progress = await self.get_progress(project_id, batch_id)
        progress_dict = progress.to_dict() if progress else {}
        now = datetime.now().isoformat()
        checkpoint = Checkpoint(
            batch_id=batch_id,
            project_id=project_id,
            progress=progress_dict,
            queue_state=queue_state or {},
            created_at=now,
        )
        await backend.save_checkpoint(project_id, batch_id, checkpoint.to_dict())
        batch_key = f"{project_id}:{batch_id}"
        self._last_checkpoint_time[batch_key] = time.time()
        logger.info(f"保存检查点: project={project_id}, batch={batch_id}")
        return checkpoint

    async def load_checkpoint(self, project_id: str, batch_id: str) -> Checkpoint | None:
        backend = self._get_backend()
        data = await backend.load_checkpoint(project_id, batch_id)
        if not data:
            return None
        logger.info(f"加载检查点: project={project_id}, batch={batch_id}")
        return Checkpoint.from_dict(data)

    async def restore_from_checkpoint(self, project_id: str, batch_id: str) -> BatchProgress | None:
        backend = self._get_backend()
        checkpoint = await self.load_checkpoint(project_id, batch_id)
        if not checkpoint:
            return None
        progress_data = checkpoint.progress
        if progress_data:
            await backend.set_progress(project_id, batch_id, progress_data)
        logger.info(f"从检查点恢复进度: project={project_id}, batch={batch_id}")
        return await self.get_progress(project_id, batch_id)

    async def _maybe_save_checkpoint(self, project_id: str, batch_id: str, progress: BatchProgress):
        batch_key = f"{project_id}:{batch_id}"
        now = time.time()
        last_time = self._last_checkpoint_time.get(batch_key, 0)
        if now - last_time >= self._checkpoint_interval:
            await self.save_checkpoint(project_id, batch_id)

    async def delete_checkpoint(self, project_id: str, batch_id: str) -> bool:
        backend = self._get_backend()
        result = await backend.delete_checkpoint(project_id, batch_id)
        logger.info(f"删除检查点: project={project_id}, batch={batch_id}")
        return result

    async def register_worker(self, project_id: str, batch_id: str, worker_id: str) -> bool:
        backend = self._get_backend()
        result = await backend.register_worker(project_id, batch_id, worker_id, self._worker_timeout)
        logger.debug(f"注册 Worker: project={project_id}, batch={batch_id}, worker={worker_id}")
        return result

    async def update_worker_heartbeat(self, project_id: str, batch_id: str, worker_id: str) -> bool:
        return await self.register_worker(project_id, batch_id, worker_id)

    async def get_active_worker_count(self, project_id: str, batch_id: str) -> int:
        backend = self._get_backend()
        workers = await backend.get_active_workers(project_id, batch_id)
        return len(workers)

    async def get_active_workers(self, project_id: str, batch_id: str) -> list:
        backend = self._get_backend()
        return await backend.get_active_workers(project_id, batch_id)

    async def unregister_worker(self, project_id: str, batch_id: str, worker_id: str) -> bool:
        backend = self._get_backend()
        result = await backend.unregister_worker(project_id, batch_id, worker_id)
        logger.debug(f"注销 Worker: project={project_id}, batch={batch_id}, worker={worker_id}")
        return result

    async def clear_progress(self, project_id: str, batch_id: str) -> bool:
        backend = self._get_backend()
        result = await backend.clear(project_id, batch_id)
        batch_key = f"{project_id}:{batch_id}"
        self._speed_history.pop(batch_key, None)
        self._last_checkpoint_time.pop(batch_key, None)
        logger.info(f"清除批次进度数据: project={project_id}, batch={batch_id}")
        return result


crawl_progress_service = CrawlProgressService()
