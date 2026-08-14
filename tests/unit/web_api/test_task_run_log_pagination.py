from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import tasks_runs
from fastapi import HTTPException, status

from tests.unit.web_api.log_pagination_test_support import FakeLogQuery


@pytest.mark.asyncio
async def test_task_execution_logs_page_database_rows(monkeypatch):
    query = FakeLogQuery(
        [SimpleNamespace(log_type="stdout", content="line-3")],
        total=8,
    )
    monkeypatch.setattr(
        tasks_runs.scheduler_service,
        "get_execution_with_permission",
        AsyncMock(return_value=SimpleNamespace(run_id="run-1")),
    )
    from antcode_core.domain.models import TaskLog

    monkeypatch.setattr(TaskLog, "filter", lambda **_kwargs: query)

    response = await tasks_runs.get_task_execution_logs(
        "run-1",
        page=3,
        size=1,
        current_user=SimpleNamespace(user_id=1),
    )

    assert response.data.pagination.total == query.total
    assert response.data.items == [{"type": "stdout", "message": "line-3"}]
    assert query.offset_value == (response.data.pagination.page - 1) * response.data.pagination.size
    assert query.limit_value == response.data.pagination.size


@pytest.mark.asyncio
async def test_task_execution_logs_rejects_response_over_byte_budget(monkeypatch):
    query = FakeLogQuery(
        [SimpleNamespace(log_type="stdout", content="x" * 200)],
        total=1,
    )
    monkeypatch.setattr(
        tasks_runs.scheduler_service,
        "get_execution_with_permission",
        AsyncMock(return_value=SimpleNamespace(run_id="run-1")),
    )
    from antcode_core.domain.models import TaskLog

    monkeypatch.setattr(TaskLog, "filter", lambda **_kwargs: query)
    monkeypatch.setattr(tasks_runs, "MAX_LOG_PAGE_BYTES", 100)

    with pytest.raises(HTTPException) as exc_info:
        await tasks_runs.get_task_execution_logs(
            "run-1",
            page=1,
            size=1,
            current_user=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE
