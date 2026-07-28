"""SSE 端点鉴权、容量和响应协议。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.log_stream_service as svc_module
from antcode_web_api.routes.v1 import log_stream as log_stream_route
from antcode_web_api.streams.log_stream_service import build_current_status_message
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.unit.web_api.fake_stream_capacity import make_broker
from tests.unit.web_api.log_stream_test_support import HistoryReader


def _stream_app() -> TestClient:
    app = FastAPI()
    app.include_router(log_stream_route.router, prefix="/api/v1/logs")
    return TestClient(app)


def test_stream_endpoint_returns_401_without_ticket():
    client = _stream_app()

    response = client.get("/api/v1/logs/runs/run-1/stream")

    assert response.status_code == 401


def test_stream_endpoint_returns_404_for_missing_run(monkeypatch):
    user = SimpleNamespace(id=7, is_admin=False)
    monkeypatch.setattr(log_stream_route, "resolve_stream_ticket", AsyncMock(return_value=(user, "jti")))
    monkeypatch.setattr(
        log_stream_route,
        "verify_execution_access",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="执行记录不存在")),
    )
    client = _stream_app()

    response = client.get("/api/v1/logs/runs/run-x/stream?ticket=t")

    assert response.status_code == 404


def test_stream_endpoint_returns_403_for_foreign_run(monkeypatch):
    user = SimpleNamespace(id=7, is_admin=False)
    monkeypatch.setattr(log_stream_route, "resolve_stream_ticket", AsyncMock(return_value=(user, "jti")))
    monkeypatch.setattr(
        log_stream_route,
        "verify_execution_access",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="无权访问此执行记录")),
    )
    client = _stream_app()

    response = client.get("/api/v1/logs/runs/run-1/stream?ticket=t")

    assert response.status_code == 403


def test_stream_endpoint_returns_429_when_capacity_exhausted(monkeypatch):
    user = SimpleNamespace(id=7, is_admin=False)
    monkeypatch.setattr(log_stream_route, "resolve_stream_ticket", AsyncMock(return_value=(user, "jti")))
    monkeypatch.setattr(log_stream_route, "verify_execution_access", AsyncMock(return_value=SimpleNamespace()))
    broker = make_broker()
    broker.max_total = 0
    monkeypatch.setattr(log_stream_route, "run_stream_broker", broker)
    client = _stream_app()

    response = client.get("/api/v1/logs/runs/run-1/stream?ticket=t")

    assert response.status_code == 429


def test_stream_endpoint_streams_sse_frames(monkeypatch):
    """走完整 HTTP 栈验证 SSE 响应头与帧解析（流由会话失效终止，避免挂起）。"""
    user = SimpleNamespace(id=7, is_admin=False)
    monkeypatch.setattr(log_stream_route, "resolve_stream_ticket", AsyncMock(return_value=(user, "jti")))
    execution = SimpleNamespace(status=SimpleNamespace(value="success"))
    monkeypatch.setattr(log_stream_route, "verify_execution_access", AsyncMock(return_value=execution))
    monkeypatch.setattr(svc_module, "SESSION_RECHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(svc_module, "_session_still_valid", AsyncMock(return_value=False))
    monkeypatch.setattr(
        svc_module,
        "ingest_log_follower",
        SimpleNamespace(
            follow=AsyncMock(),
            unfollow=AsyncMock(),
            history_reader=HistoryReader([]),
        ),
    )
    broker = make_broker()
    monkeypatch.setattr(log_stream_route, "run_stream_broker", broker)
    monkeypatch.setattr(svc_module, "run_stream_broker", broker)
    client = _stream_app()

    with client.stream("GET", "/api/v1/logs/runs/run-1/stream?ticket=t") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert "no-transform" in response.headers["cache-control"]
        body = b"".join(response.iter_bytes())

    events = [line.removeprefix("event: ") for line in body.decode().splitlines() if line.startswith("event: ")]
    assert events == ["run_status", "historical_logs_start", "no_historical_logs", "stream_error"]


def test_build_current_status_message_maps_progress_and_text():
    for status, expected_message, expected_progress in [
        ("running", "任务正在执行中", None),
        ("success", "任务执行成功", 100.0),
        ("failed", "任务执行失败", 100.0),
        ("queued", "任务排队中", None),
    ]:
        message = build_current_status_message("run-1", SimpleNamespace(status=SimpleNamespace(value=status)))
        assert message["data"]["status"] == status
        assert message["data"]["message"] == expected_message
        assert message["data"]["progress"] == expected_progress
