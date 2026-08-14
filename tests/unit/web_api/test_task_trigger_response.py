from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import tasks_execute

RUN_ID = "8a0c9ea5-8344-51c3-956d-993c10c9f482"


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [tasks_execute.trigger_task, tasks_execute.execute_task])
async def test_trigger_handlers_return_deterministic_outbox_run_id(monkeypatch, handler) -> None:
    monkeypatch.setattr(tasks_execute, "_acquire_trigger_dedup_lock", AsyncMock(return_value=True))
    trigger = AsyncMock(return_value=RUN_ID)
    monkeypatch.setattr(tasks_execute.scheduler_service, "trigger_task_by_user", trigger)
    user = SimpleNamespace(user_id=7)

    if handler is tasks_execute.execute_task:
        response = await handler("task-public", tasks_execute.TaskExecuteRequest(), user)
    else:
        response = await handler("task-public", user)

    assert response.data == {"task_id": "task-public", "run_id": RUN_ID, "triggered": True}
    trigger.assert_awaited_once_with("task-public", 7)


@pytest.mark.asyncio
async def test_trigger_handler_keeps_mocked_legacy_boolean_compatible(monkeypatch) -> None:
    monkeypatch.setattr(tasks_execute, "_acquire_trigger_dedup_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(tasks_execute.scheduler_service, "trigger_task_by_user", AsyncMock(return_value=True))

    response = await tasks_execute.trigger_task("task-public", SimpleNamespace(user_id=7))

    assert response.data == {"task_id": "task-public", "run_id": None, "triggered": True}
