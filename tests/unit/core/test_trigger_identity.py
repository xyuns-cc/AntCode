from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.scheduler.scheduler_service import SchedulerService
from antcode_core.application.services.scheduler.trigger_identity import trigger_run_id


def test_trigger_run_id_is_stable_and_key_scoped() -> None:
    assert trigger_run_id(42, "outbox-1") == trigger_run_id("42", "outbox-1")
    assert trigger_run_id(42, "outbox-1") != trigger_run_id(42, "outbox-2")


@pytest.mark.asyncio
async def test_control_plane_trigger_returns_outbox_derived_run_id(monkeypatch) -> None:
    service = SchedulerService()
    publish = AsyncMock(return_value=SimpleNamespace(public_id="outbox-1"))
    monkeypatch.setattr(service, "_publish_event", publish)

    run_id = await service.trigger_task(42)

    assert run_id == trigger_run_id(42, "outbox-1")
    publish.assert_awaited_once_with("task_trigger", 42)
