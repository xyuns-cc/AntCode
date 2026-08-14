from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_master.control import scheduler_loop, scheduler_task_events
from antcode_master.control.manual_retry_lineage import attach_manual_retry_lineage
from tortoise import Tortoise


@pytest_asyncio.fixture
async def lineage_database():
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["antcode_core.domain.models.task_run"]})
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_manual_retry_lineage_is_durable_and_idempotent(lineage_database):
    source = await TaskRun.create(task_id=7, run_id="manual-source", status=TaskStatus.FAILED)
    target = await TaskRun.create(
        task_id=7,
        run_id="manual-target",
        status=TaskStatus.QUEUED,
        result_data={"existing": True},
    )

    await attach_manual_retry_lineage(target.run_id, source.run_id, source.task_id)
    await attach_manual_retry_lineage(target.run_id, source.run_id, source.task_id)

    await target.refresh_from_db()
    assert target.result_data == {"existing": True, "retry_source_run_id": source.run_id}


@pytest.mark.asyncio
async def test_manual_retry_lineage_rejects_non_terminal_source(lineage_database):
    source = await TaskRun.create(task_id=7, run_id="running-source", status=TaskStatus.RUNNING)
    target = await TaskRun.create(task_id=7, run_id="invalid-target", status=TaskStatus.QUEUED)

    with pytest.raises(RuntimeError, match="source 已失效"):
        await attach_manual_retry_lineage(target.run_id, source.run_id, source.task_id)


@pytest.mark.asyncio
async def test_task_trigger_attaches_manual_retry_lineage(monkeypatch):
    trigger = AsyncMock(return_value="manual-target")
    attach = AsyncMock()
    monkeypatch.setattr(scheduler_loop.scheduler_service, "trigger_task", trigger)
    monkeypatch.setattr(scheduler_task_events, "attach_manual_retry_lineage", attach)
    payload = {
        "task_id": "7",
        "outbox_id": "manual-outbox",
        "manual_retry_source_run_id": "manual-source",
    }

    handled = await scheduler_task_events.dispatch_task_event(payload, "task_trigger")

    assert handled is True
    trigger.assert_awaited_once_with(7, idempotency_key="manual-outbox")
    attach.assert_awaited_once_with("manual-target", "manual-source", 7)
