"""批次进度服务

基于抽象后端实现批次进度管理，支持：
- 进度更新和查询
- 检查点保存和加载
- 速度计算

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

import time
from datetime import datetime

from loguru import logger

from antcode_core.application.services.base import BaseService
from antcode_core.application.services.crawl.backends.progress_backend import (
    ProgressStore,
    get_progress_store,
)
from antcode_core.application.services.crawl.progress_lifecycle import CrawlProgressLifecycleMixin
from antcode_core.application.services.crawl.progress_models import BatchProgress, Checkpoint

__all__ = ["BatchProgress", "Checkpoint", "CrawlProgressService", "crawl_progress_service"]

DEFAULT_CHECKPOINT_INTERVAL = 60
DEFAULT_SPEED_WINDOW = 60
DEFAULT_WORKER_TIMEOUT = 120
MIN_SPEED_SAMPLES = 2


class CrawlProgressService(BaseService, CrawlProgressLifecycleMixin):
    """批次进度服务"""

    def __init__(
        self,
        backend: ProgressStore | None = None,
        checkpoint_interval: int | None = None,
        speed_window: int | None = None,
        worker_timeout: int | None = None,
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
        self._require_active_write(
            await backend.set_progress(project_id, batch_id, progress.to_dict()),
            operation="初始化进度",
        )
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
        self._require_active_write(
            await backend.update_progress(project_id, batch_id, updates),
            operation="更新进度",
        )
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
        logger.debug(
            f"更新批次进度: project={project_id}, batch={batch_id}, completed={completed}, failed={failed}, new_urls={new_urls}"
        )
        await self._maybe_save_checkpoint(project_id, batch_id, progress)
        return progress

    async def sync_progress_counters(
        self,
        project_id: str,
        batch_id: str,
        *,
        total_urls: int,
        completed_urls: int,
        failed_urls: int,
        pending_urls: int,
        active_workers: int = 0,
    ) -> None:
        """接缝修复（R2 seam-5）：用权威值（DB run 状态聚合）覆写进度计数。

        背景：crawl 主链路（batch_dispatcher → TaskRun → worker）**不经过**
        ``queue_service``，全链路没有任何环节调用 ``update_progress`` 做增量
        计数，进度 hash 自 ``init_progress`` 后永远停留在
        ``completed=0 / pending=total``。由 master 的
        ``crawl_batch_status_loop`` 周期性把 TaskRun 状态聚合同步进来。

        语义是**绝对值覆写**（幂等），不做增量加法——单一写者 + 幂等覆写，
        天然避免"sink 报一次、gateway 又加一次"式双计。
        """
        backend = self._get_backend()
        batch_key = f"{project_id}:{batch_id}"
        await self._update_speed_history(batch_key, completed_urls)
        speed = await self._calculate_speed(batch_key)
        updated = await backend.update_progress(
            project_id,
            batch_id,
            {
                # batch_id / project_id 一并写入：进度 hash 可能已被
                # clear_progress 清掉（如 API 侧清理），HSET 需重建完整字段。
                "batch_id": batch_id,
                "project_id": project_id,
                "total_urls": int(total_urls),
                "pending_urls": int(pending_urls),
                "completed_urls": int(completed_urls),
                "failed_urls": int(failed_urls),
                "active_workers": int(active_workers),
                "speed_per_minute": speed,
                "last_updated": datetime.now().isoformat(),
            },
        )
        if not updated:
            raise RuntimeError("Crawl 批次已取消，权威进度写入被 fence 拒绝")

    async def increment_total_urls(self, project_id: str, batch_id: str, count: int) -> int:
        backend = self._get_backend()
        new_total = await backend.increment_progress(project_id, batch_id, "total_urls", count)
        await backend.increment_progress(project_id, batch_id, "pending_urls", count)
        self._require_active_write(
            await backend.update_progress(project_id, batch_id, {"last_updated": datetime.now().isoformat()}),
            operation="更新进度时间",
        )
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
        if len(history) < MIN_SPEED_SAMPLES:
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

    @staticmethod
    def _require_active_write(updated: bool, *, operation: str) -> None:
        if not updated:
            raise RuntimeError(f"Crawl 批次已取消，{operation}被 fence 拒绝")


crawl_progress_service = CrawlProgressService()
