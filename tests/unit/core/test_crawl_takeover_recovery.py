from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from antcode_core.application.services.crawl.backends import (
    CrawlQueueBackend,
    QueueProjectDiscovery,
    QueueTask,
    ReclaimedTask,
)
from antcode_core.application.services.crawl.takeover_recovery_service import (
    CrawlTakeoverRecoveryService,
    TakeoverRecoveryConfig,
)
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.pending_summary import parse_pending_summary
from antcode_core.infrastructure.redis.stream_records import parse_pending_message


class _FakeQueueBackend:
    def __init__(self, pending: dict[str, list[ReclaimedTask]]) -> None:
        self.pending = pending
        self.failed_projects: set[str] = set()
        self.failed_messages: set[str] = set()
        self.discovery_failures: tuple[str, ...] = ()

    async def list_project_ids(self) -> list[str]:
        return sorted(self.pending)

    async def discover_projects(self) -> QueueProjectDiscovery:
        return QueueProjectDiscovery(tuple(sorted(self.pending)), self.discovery_failures)

    async def get_pending_count(self, project_id: str, priority=None) -> int:
        return len(self.pending[project_id])

    async def reclaim(self, project_id: str, min_idle_ms=0, count=100) -> list[ReclaimedTask]:
        if project_id in self.failed_projects:
            raise RuntimeError("scan unavailable")
        return list(self.pending[project_id][:count])

    async def requeue_claimed(self, project_id: str, task: QueueTask) -> str | None:
        return await self._move(project_id, task)

    async def dead_letter_claimed(self, project_id: str, task: QueueTask) -> str | None:
        return await self._move(project_id, task)

    async def _move(self, project_id: str, task: QueueTask) -> str | None:
        if task.msg_id in self.failed_messages:
            raise RuntimeError("atomic move failed")
        items = self.pending[project_id]
        match = next((item for item in items if item.task.msg_id == task.msg_id), None)
        if match is None:
            return None
        items.remove(match)
        return f"new-{task.msg_id}"


def _reclaimed(msg_id: str, *, delivery_count: int = 1) -> ReclaimedTask:
    task = QueueTask(
        msg_id=msg_id,
        url=f"https://example.test/{msg_id}",
        project_id="project",
        priority=Priority.NORMAL,
    )
    return ReclaimedTask(task=task, delivery_count=delivery_count)


def _service(
    backend: _FakeQueueBackend,
    *,
    batch_ids: list[str] | None = None,
    recoverer: Callable[[str], Awaitable[None]] | None = None,
) -> CrawlTakeoverRecoveryService:
    async def load_batches() -> list[str]:
        return list(batch_ids or [])

    async def recover_batch(_batch_id: str) -> None:
        return None

    return CrawlTakeoverRecoveryService(
        cast(CrawlQueueBackend, backend),
        config=TakeoverRecoveryConfig(timeout_ms=0, batch_size=10, max_retries=3),
        batch_id_loader=load_batches,
        batch_recoverer=recoverer or recover_batch,
    )


@pytest.mark.asyncio
async def test_takeover_recovery_requeues_dead_letters_and_is_idempotent() -> None:
    backend = _FakeQueueBackend({"project-a": [_reclaimed("retry"), _reclaimed("dead", delivery_count=5)]})
    service = _service(backend)

    first = await service.recover()
    second = await service.recover()

    assert first.tasks_requeued == 1
    assert first.tasks_dead_lettered == 1
    assert second.tasks_requeued == 0
    assert second.tasks_dead_lettered == 0


@pytest.mark.asyncio
async def test_takeover_recovery_exposes_partial_failures_after_other_work() -> None:
    backend = _FakeQueueBackend(
        {
            "project-a": [_reclaimed("ok")],
            "project-b": [_reclaimed("broken")],
        }
    )
    backend.failed_messages.add("broken")

    async def recover_batch(batch_id: str) -> None:
        if batch_id == "batch-b":
            raise RuntimeError("dispatch failed")

    service = _service(
        backend,
        batch_ids=["batch-a", "batch-b"],
        recoverer=recover_batch,
    )

    # C1: 部分失败不再抛异常，失败项记录在 report.failures 里留待后续重试
    report = await service.recover()

    assert report.tasks_requeued == 1
    assert report.batches_recovered == 1
    assert len(report.failures) == 2
    assert "project-b" in report.failures[0]
    assert "batch-b" in report.failures[1]


@pytest.mark.asyncio
async def test_takeover_recovers_active_project_when_fenced_residual_is_reported() -> None:
    backend = _FakeQueueBackend({"active": [_reclaimed("ok")]})
    backend.discovery_failures = ("已删除 Crawl 项目仍存在 Stream: project=deleted",)

    report = await _service(backend).recover()

    assert report.projects_scanned == 1
    assert report.tasks_requeued == 1
    assert report.failures == backend.discovery_failures


def test_pending_parsers_support_current_redis_dict_shape() -> None:
    summary = parse_pending_summary(
        {
            "pending": 1,
            "min": b"1-0",
            "max": b"1-0",
            "consumers": [{"name": b"worker-a", "pending": 1}],
        }
    )
    message = parse_pending_message(
        {
            "message_id": b"1-0",
            "consumer": b"worker-a",
            "time_since_delivered": 100,
            "times_delivered": 2,
        }
    )

    assert summary["pending_count"] == 1
    assert summary["consumers"] == {"worker-a": 1}
    assert message.msg_id == "1-0"
    assert message.delivery_count == 2
