from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import TaskStatus
from antcode_web_api.routes.v1 import runs, tasks_runs


def _assigned_execution():
    return SimpleNamespace(
        run_id="run-1",
        worker_id=7,
        status=TaskStatus.RUNNING,
    )


@pytest.mark.asyncio
async def test_cancel_run_records_request_before_control_and_reports_honestly(monkeypatch) -> None:
    events: list[str] = []

    async def record(_run_id, _user_id):
        events.append("record")
        return True

    async def send(_execution, _user_id):
        events.append("send")
        return True

    mark_terminal = AsyncMock()
    monkeypatch.setattr(runs, "_get_cancellable_execution", AsyncMock(return_value=_assigned_execution()))
    monkeypatch.setattr(runs, "_record_assigned_cancel_request", record)
    monkeypatch.setattr(runs, "_send_worker_cancel", send)
    monkeypatch.setattr(runs, "_mark_execution_cancelled", mark_terminal)

    response = await runs.cancel_run("run-1", SimpleNamespace(user_id=3))

    assert events == ["record", "send"]
    assert response.data == {
        "run_id": "run-1",
        "status": "cancel_requested",
        "remote_cancelled": True,
    }
    mark_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_route_waits_for_worker_terminal_result(monkeypatch, http_request) -> None:
    record = AsyncMock(return_value=True)
    mark_terminal = AsyncMock()
    monkeypatch.setattr(tasks_runs, "_get_stoppable_execution", AsyncMock(return_value=_assigned_execution()))
    monkeypatch.setattr(tasks_runs, "record_task_cancel_request", record)
    monkeypatch.setattr(tasks_runs, "_try_send_stop_event_with_reason", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(tasks_runs, "mark_task_run_cancelled", mark_terminal)

    response = await tasks_runs.stop_task_execution("run-1", SimpleNamespace(user_id=3), http_request=http_request)

    assert response.data["status"] == "cancel_requested"
    assert response.data["remote_cancelled"] is True
    record.assert_awaited_once_with("run-1", 3)
    mark_terminal.assert_not_awaited()
