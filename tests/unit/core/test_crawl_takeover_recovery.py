from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from antcode_core.application.services.crawl.backends import (
    CrawlQueueBackend,
    QueueTask,
    ReclaimedTask,
)
from antcode_core.application.services.crawl.election_service import (
    MASTER_LOCK_KEY,
    MasterElectionService,
)
from antcode_core.application.services.crawl.takeover_recovery_service import (
    CrawlTakeoverRecoveryService,
    TakeoverRecoveryConfig,
    TakeoverRecoveryError,
    TakeoverRecoveryReport,
)
from antcode_core.domain.models.enums import Priority
from antcode_core.infrastructure.redis.stream_client import StreamClient


class _FakeQueueBackend:
    def __init__(self, pending: dict[str, list[ReclaimedTask]]) -> None:
        self.pending = pending
        self.failed_projects: set[str] = set()
        self.failed_messages: set[str] = set()

    async def list_project_ids(self) -> list[str]:
        return sorted(self.pending)

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


class _FakeRedis:
    def __init__(self) -> None:
        self.holder: bytes | None = None

    async def get(self, key: str) -> bytes | None:
        assert key == MASTER_LOCK_KEY
        return self.holder

    async def set(self, key: str, value: str, **_kwargs) -> bool:
        assert key == MASTER_LOCK_KEY
        if self.holder is not None:
            return False
        self.holder = value.encode()
        return True

    async def eval(self, *args) -> int:
        _, _, key, worker_id, *_ = args
        if key == MASTER_LOCK_KEY and self.holder == worker_id.encode():
            self.holder = None
            return 1
        return 0


class _RecoveryStub:
    def __init__(self, report: TakeoverRecoveryReport | None = None) -> None:
        self.report = report or TakeoverRecoveryReport()
        self.error: TakeoverRecoveryError | None = None
        self.calls = 0

    async def recover(self) -> TakeoverRecoveryReport:
        self.calls += 1
        if self.error:
            raise self.error
        return self.report


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
    backend = _FakeQueueBackend({"project-a": [_reclaimed("retry"), _reclaimed("dead", delivery_count=4)]})
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
async def test_initial_leader_activation_runs_recovery() -> None:
    redis = _FakeRedis()
    recovery = _RecoveryStub(TakeoverRecoveryReport(projects_scanned=1))
    election = MasterElectionService(
        "master-a",
        redis_client=redis,
        enable_background_tasks=False,
        takeover_recovery=recovery,
    )

    await election.start()

    assert recovery.calls == 1
    assert await election.is_leader() is True


@pytest.mark.asyncio
async def test_takeover_recovery_failure_does_not_block_leadership() -> None:
    """C1: 恢复失败（甚至抛异常）不得导致 resign——否则单个顽固失败的
    批次会让集群陷入 acquire→恢复失败→resign 的无主循环。"""
    redis = _FakeRedis()
    recovery = _RecoveryStub()
    recovery.error = TakeoverRecoveryError(TakeoverRecoveryReport(failures=("project-a recovery failed",)))
    election = MasterElectionService(
        "master-b",
        redis_client=redis,
        enable_background_tasks=False,
        takeover_recovery=recovery,
    )

    assert await election.watch_leader() is True

    assert recovery.calls == 1
    assert redis.holder == b"master-b"
    assert await election.is_leader() is True


def test_pending_parsers_support_current_redis_dict_shape() -> None:
    summary = StreamClient._parse_pending_summary(
        {
            "pending": 1,
            "min": b"1-0",
            "max": b"1-0",
            "consumers": [{"name": b"worker-a", "pending": 1}],
        }
    )
    message = StreamClient._parse_pending_message(
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
