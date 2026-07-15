import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.streams.log_stream_service import build_current_status_message
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[3]
LOGS_ROUTE_PATH = ROOT / "services/web_api/src/antcode_web_api/routes/v1/logs.py"
spec = importlib.util.spec_from_file_location("logs_route_under_test", LOGS_ROUTE_PATH)
logs_route = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(logs_route)


@pytest.mark.asyncio
async def test_get_run_logs_accepts_string_log_type_for_raw(monkeypatch):
    execution = SimpleNamespace(
        start_time=datetime.now(UTC),
        created_at=datetime.now(UTC),
        task_id="task-1",
    )

    monkeypatch.setattr(
        logs_route.log_security_service,
        "verify_log_access_permission",
        AsyncMock(return_value=execution),
    )
    monkeypatch.setattr(
        logs_route.task_log_service,
        "get_execution_logs",
        AsyncMock(return_value={"output": "hello", "error": ""}),
    )

    response = await logs_route.get_run_logs(
        run_id="run-1",
        format="raw",
        log_type="stdout",
        level=None,
        lines=None,
        search=None,
        current_user=SimpleNamespace(user_id=1),
    )

    assert response.success is True
    assert response.data.log_type == "stdout"
    assert response.data.raw_content == "hello"


@pytest.mark.asyncio
async def test_raw_logs_read_history_from_postgres_service(monkeypatch):
    execution = SimpleNamespace(
        start_time=datetime.now(UTC),
        created_at=datetime.now(UTC),
        task_id="task-1",
    )

    monkeypatch.setattr(
        logs_route.log_security_service,
        "verify_log_access_permission",
        AsyncMock(return_value=execution),
    )
    monkeypatch.setattr(
        logs_route.task_log_service,
        "get_execution_logs",
        AsyncMock(return_value={"output": "pg-out", "error": "pg-err"}),
    )

    response = await logs_route.get_run_logs(
        run_id="run-1",
        format="raw",
        log_type=None,
        level=None,
        lines=None,
        search=None,
        current_user=SimpleNamespace(user_id=1),
    )

    assert response.success is True
    assert "pg-out" in response.data.raw_content
    assert "pg-err" in response.data.raw_content


def test_logs_route_has_no_object_storage_history_reader():
    source = logs_route.__loader__.get_source(logs_route.__name__)

    assert "_get_logs_from_s3" not in source
    assert "get_log_storage" not in source
    assert "presigned" not in source.lower()
    assert "aiohttp" not in source


@pytest.mark.asyncio
async def test_get_run_logs_rejects_invalid_format(monkeypatch):
    monkeypatch.setattr(
        logs_route.log_security_service,
        "verify_log_access_permission",
        AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await logs_route.get_run_logs(
            run_id="run-1",
            format="invalid",
            log_type=None,
            level=None,
            lines=None,
            search=None,
            current_user=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == 400
    assert "日志格式" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "status, expected_message, expected_progress",
    [
        ("running", "任务正在执行中", None),
        ("success", "任务执行成功", 100.0),
        ("failed", "任务执行失败", 100.0),
        ("queued", "任务排队中", None),
    ],
)
def test_current_status_message_maps_message_and_progress(
    status,
    expected_message,
    expected_progress,
):
    execution = SimpleNamespace(status=SimpleNamespace(value=status))

    message = build_current_status_message("run-1", execution)

    assert message["type"] == "run_status"
    assert message["run_id"] == "run-1"
    assert message["data"]["status"] == status
    assert message["data"]["progress"] == expected_progress
    assert message["data"]["message"] == expected_message
