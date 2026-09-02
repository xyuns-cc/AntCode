import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from antcode_core.application.services.crawl.batch_dispatcher_service import (
    crawl_batch_dispatcher_service,
)
from antcode_core.application.services.scheduler.outbox_service import OutboxConsumeClaim
from antcode_master.control import scheduler_event_handlers, scheduler_loop, task_change_handler
from antcode_master.control.scheduler_authority import SchedulerAuthorityLost
from antcode_master.control.scheduler_event_loop import (
    LeaderAuthorityLost,
    OutboxClaimBusy,
    SchedulerEventLoop,
)

scheduler_event_module = importlib.import_module("antcode_master.control.scheduler_event_loop")


@pytest.fixture(autouse=True)
def _leader_authority(monkeypatch):
    monkeypatch.setattr(scheduler_event_module, "ensure_leader", AsyncMock(return_value=True))


class _FakeDurableInbox:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.owner = None
        self.consumed = False
        self.claims = 0
        self.releases = 0
        self.completions = 0

    async def claim_consumption(self, _outbox_id, owner):
        async with self._lock:
            if self.consumed:
                return OutboxConsumeClaim.CONSUMED
            if self.owner is not None:
                return OutboxConsumeClaim.BUSY
            self.owner = owner
            self.claims += 1
            return OutboxConsumeClaim.CLAIMED

    async def heartbeat_consumption(self, _outbox_id, owner):
        assert self.owner == owner

    async def complete_consumption(self, _outbox_id, owner):
        assert self.owner == owner
        self.owner = None
        self.consumed = True
        self.completions += 1

    async def release_consumption(self, _outbox_id, owner):
        if self.owner == owner:
            self.owner = None
            self.releases += 1


def _event():
    return {
        "event": "batch_started",
        "batch_id": "batch-1",
        "outbox_id": "outbox-1",
    }


@pytest.mark.asyncio
async def test_two_master_instances_cannot_execute_same_outbox_concurrently(monkeypatch):
    inbox = _FakeDurableInbox()
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def handler(_event, _batch_id):
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()

    monkeypatch.setattr(scheduler_event_module, "scheduler_outbox_service", inbox)
    monkeypatch.setattr(crawl_batch_dispatcher_service, "handle_batch_event", handler)
    first = SchedulerEventLoop()
    second = SchedulerEventLoop()

    first_task = asyncio.create_task(first._handle_message(_event()))
    await started.wait()
    with pytest.raises(OutboxClaimBusy):
        await second._handle_message(_event())
    finish.set()
    await first_task

    assert calls == 1
    assert inbox.completions == 1


@pytest.mark.asyncio
async def test_business_failure_releases_claim_for_redelivery(monkeypatch):
    inbox = _FakeDurableInbox()
    handler = AsyncMock(side_effect=[RuntimeError("temporary failure"), None])
    monkeypatch.setattr(scheduler_event_module, "scheduler_outbox_service", inbox)
    monkeypatch.setattr(crawl_batch_dispatcher_service, "handle_batch_event", handler)
    loop = SchedulerEventLoop()

    with pytest.raises(RuntimeError, match="temporary failure"):
        await loop._handle_message(_event())
    await loop._handle_message(_event())

    assert handler.await_count == 2
    assert inbox.releases == 1
    assert inbox.completions == 1


@pytest.mark.asyncio
async def test_consumed_duplicate_is_acked_without_reexecuting_business(monkeypatch):
    inbox = _FakeDurableInbox()
    handler = AsyncMock()
    monkeypatch.setattr(scheduler_event_module, "scheduler_outbox_service", inbox)
    monkeypatch.setattr(crawl_batch_dispatcher_service, "handle_batch_event", handler)
    loop = SchedulerEventLoop()

    await loop._handle_message(_event())
    await loop._handle_message(_event())

    handler.assert_awaited_once()
    assert inbox.claims == 1
    assert inbox.completions == 1


@pytest.mark.asyncio
async def test_leader_loss_releases_claim_before_outbox_completion(monkeypatch):
    inbox = _FakeDurableInbox()
    monkeypatch.setattr(scheduler_event_module, "scheduler_outbox_service", inbox)
    monkeypatch.setattr(scheduler_event_module, "ensure_leader", AsyncMock(return_value=False))
    loop = SchedulerEventLoop()
    loop._dispatch_with_claim_heartbeat = AsyncMock()

    with pytest.raises(LeaderAuthorityLost):
        await loop._handle_message(_event())

    assert inbox.releases == 1
    assert inbox.completions == 0


@pytest.mark.asyncio
async def test_scheduler_authority_loss_keeps_pel_without_poison_counting() -> None:
    loop = SchedulerEventLoop()
    loop._handle_message = AsyncMock(side_effect=SchedulerAuthorityLost("stale"))
    loop._handle_processing_failure = AsyncMock()
    message = SimpleNamespace(msg_id="1-0", data={"event": "task_trigger"})

    assert await loop._process_message(message) is False
    loop._handle_processing_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_spider_cleanup_event_deletes_all_run_storage(monkeypatch):
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[])
    redis = MagicMock()
    redis.pipeline.return_value = pipeline
    monkeypatch.setattr(scheduler_event_handlers, "get_redis_client", AsyncMock(return_value=redis))
    loop = SchedulerEventLoop()

    await loop._dispatch_message(
        {
            "event": "spider_storage_cleanup",
            "run_ids": ["run-1"],
            "project_id": "project-1",
        }
    )

    cleanup_args = pipeline.eval.call_args.args
    assert cleanup_args[1] == 5
    assert cleanup_args[2].endswith("spider:run-1:tombstone")
    deleted = cleanup_args[3:]
    assert any(key.endswith("spider:run-1:data") for key in deleted)
    assert any(key.endswith("spider:run-1:meta") for key in deleted)
    assert any(key.endswith("spider:run-1:item-ids") for key in deleted)
    assert any(key.endswith("spider:run-1:item-order") for key in deleted)
    assert pipeline.zrem.call_args_list == [
        call("{antcode}:spider:index:project-1", "run-1"),
        call("{antcode}:spider:index:expiry:project-1", "run-1"),
    ]
    assert pipeline.execute.await_count == 2


@pytest.mark.asyncio
async def test_crawl_project_cleanup_event_runs_idempotent_primitive(monkeypatch):
    from antcode_core.application.services.crawl import project_redis_cleanup

    cleanup = AsyncMock(
        return_value=SimpleNamespace(
            project_id="project-1",
            batch_count=2,
        )
    )
    monkeypatch.setattr(project_redis_cleanup.crawl_project_redis_cleanup, "cleanup", cleanup)

    await SchedulerEventLoop()._dispatch_message(
        {
            "event": "crawl_project_cleanup",
            "project_id": "project-1",
            "batch_ids": ["batch-1", "batch-2"],
        }
    )

    request = cleanup.await_args.args[0]
    assert request.project_id == "project-1"
    assert request.batch_ids == ("batch-1", "batch-2")


@pytest.mark.asyncio
async def test_crawl_project_cleanup_failure_stays_retryable(monkeypatch):
    from antcode_core.application.services.crawl import project_redis_cleanup

    cleanup = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    monkeypatch.setattr(project_redis_cleanup.crawl_project_redis_cleanup, "cleanup", cleanup)

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await SchedulerEventLoop()._dispatch_message(
            {
                "event": "crawl_project_cleanup",
                "project_id": "project-1",
                "batch_ids": ["batch-1"],
            }
        )


@pytest.mark.asyncio
async def test_deleted_task_event_cleans_retry_queue_before_ack(monkeypatch):
    cleanup = AsyncMock(return_value=2)
    remove_task = AsyncMock()
    monkeypatch.setattr(task_change_handler.Task, "get_or_none", AsyncMock(return_value=None))
    monkeypatch.setattr(task_change_handler, "cleanup_deleted_task_retries", cleanup)
    monkeypatch.setattr(scheduler_loop.scheduler_service, "remove_task", remove_task)

    await SchedulerEventLoop()._dispatch_message({"event": "task_changed", "task_id": "7"})

    cleanup.assert_awaited_once_with(7)
    remove_task.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_outbox_dlq_requires_durable_requeue_before_ack(monkeypatch):
    loop = SchedulerEventLoop()
    loop._write_to_dlq = AsyncMock(return_value=True)
    requeue = AsyncMock()
    monkeypatch.setattr(scheduler_event_module.scheduler_outbox_service, "requeue_consumption_failure", requeue)
    message = SimpleNamespace(
        msg_id="1-0",
        data={"event": "spider_storage_cleanup", "outbox_id": "outbox-1"},
    )

    should_ack = await loop._dead_letter_failed_message(message, RuntimeError("redis unavailable"), 5)

    assert should_ack is True
    requeue.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_dlq_keeps_pel_when_durable_requeue_fails(monkeypatch):
    loop = SchedulerEventLoop()
    loop._write_to_dlq = AsyncMock(return_value=True)
    requeue = AsyncMock(side_effect=RuntimeError("postgres unavailable"))
    monkeypatch.setattr(scheduler_event_module.scheduler_outbox_service, "requeue_consumption_failure", requeue)
    message = SimpleNamespace(msg_id="1-0", data={"outbox_id": "outbox-1"})

    should_ack = await loop._dead_letter_failed_message(message, RuntimeError("cleanup failed"), 5)

    assert should_ack is False
