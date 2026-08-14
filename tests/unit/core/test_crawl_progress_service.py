"""Crawl progress service public API and lifecycle contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.crawl.progress_models import (
    BatchProgress as ProgressModel,
)
from antcode_core.application.services.crawl.progress_models import (
    Checkpoint as CheckpointModel,
)
from antcode_core.application.services.crawl.progress_service import (
    BatchProgress,
    Checkpoint,
    CrawlProgressService,
)


def test_progress_models_remain_exported_from_service_module() -> None:
    assert BatchProgress is ProgressModel
    assert Checkpoint is CheckpointModel


@pytest.mark.asyncio
async def test_checkpoint_write_exposes_cancel_fence_rejection() -> None:
    backend = AsyncMock()
    backend.get_progress.return_value = None
    backend.save_checkpoint.return_value = False
    service = CrawlProgressService(backend=backend)

    with pytest.raises(RuntimeError, match="保存检查点.*fence"):
        await service.save_checkpoint("project-1", "batch-1")


@pytest.mark.asyncio
async def test_checkpoint_restore_exposes_cancel_fence_rejection() -> None:
    backend = AsyncMock()
    backend.load_checkpoint.return_value = {
        "batch_id": "batch-1",
        "project_id": "project-1",
        "progress": {"completed_urls": 1},
    }
    backend.set_progress.return_value = False
    service = CrawlProgressService(backend=backend)

    with pytest.raises(RuntimeError, match="恢复检查点.*fence"):
        await service.restore_from_checkpoint("project-1", "batch-1")


@pytest.mark.asyncio
async def test_cancel_progress_clears_local_runtime_state() -> None:
    backend = AsyncMock()
    backend.fence_and_clear.return_value = True
    service = CrawlProgressService(backend=backend)
    service._speed_history["project-1:batch-1"] = [(1.0, 1)]
    service._last_checkpoint_time["project-1:batch-1"] = 1.0

    assert await service.cancel_progress("project-1", "batch-1") is True
    assert service._speed_history == {}
    assert service._last_checkpoint_time == {}
    backend.fence_and_clear.assert_awaited_once_with("project-1", "batch-1")
