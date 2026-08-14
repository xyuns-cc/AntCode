from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.schemas.task import TaskUpdateRequest
from antcode_web_api.routes.v1 import tasks
from fastapi import HTTPException

BAD_REQUEST = 400


@pytest.mark.asyncio
async def test_task_update_maps_schedule_validation_error_to_bad_request(monkeypatch) -> None:
    validation_error = ValueError("DATE 调度必须提供 scheduled_time")
    monkeypatch.setattr(tasks, "_ensure_specified_worker_access", AsyncMock())
    monkeypatch.setattr(
        tasks.scheduler_service,
        "update_task",
        AsyncMock(side_effect=validation_error),
    )

    with pytest.raises(HTTPException) as captured:
        await tasks.update_task(
            "task-public",
            TaskUpdateRequest(name="updated-task"),
            SimpleNamespace(user_id=7),
        )

    assert captured.value.status_code == BAD_REQUEST
    assert captured.value.detail == "DATE 调度必须提供 scheduled_time"
