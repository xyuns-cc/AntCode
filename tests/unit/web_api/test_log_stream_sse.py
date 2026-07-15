"""SSE 日志流：帧序列、历史/实时重叠过滤、会话重校验、端点鉴权语义。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.log_stream_service as svc_module
import pytest
from antcode_web_api.routes.v1 import log_stream as log_stream_route
from antcode_web_api.streams.log_stream_service import (
    LogStreamService,
    build_current_status_message,
)
from antcode_web_api.streams.run_stream_broker import QUEUE_OVERFLOW, RunStreamBroker
from antcode_web_api.streams.sse import build_log_line_message
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _parse_frame(frame: bytes) -> tuple[str, dict]:
    text = frame.decode("utf-8")
    assert text.endswith("\n\n")
    event_line, data_line = text.strip().split("\n")
    assert event_line.startswith("event: ")
    assert data_line.startswith("data: ")
    return event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))


def _history_entry(log_type: str, content: str, sequence: int | None) -> dict:
    return {
        "log_type": log_type,
        "content": content,
        "timestamp": "",
        "sequence": sequence,
        "source": "pg_history",
    }


def _setup(monkeypatch, history: list[dict], status: str = "running"):
    broker = RunStreamBroker()
    follower = SimpleNamespace(
        follow=AsyncMock(),
        unfollow=AsyncMock(),
        fetch_history=AsyncMock(return_value=history),
    )
    monkeypatch.setattr(svc_module, "run_stream_broker", broker)
    monkeypatch.setattr(svc_module, "ingest_log_follower", follower)
    execution = SimpleNamespace(status=SimpleNamespace(value=status))
    generator = LogStreamService().stream("run-1", execution, user_id=7, session_jti="jti")
    return broker, follower, generator


async def _drain_until_history_end(generator) -> list[tuple[str, dict]]:
    """消费 run_status → historical_logs_start → log_line* → 历史结束帧。"""
    frames = [_parse_frame(await anext(generator))]
    assert frames[0][0] == "run_status"
    frames.append(_parse_frame(await anext(generator)))
    assert frames[1][0] == "historical_logs_start"
    while True:
        event, data = _parse_frame(await anext(generator))
        frames.append((event, data))
        if event in {"historical_logs_end", "no_historical_logs"}:
            return frames


@pytest.mark.asyncio
async def test_frame_sequence_history_then_realtime(monkeypatch):
    history = [
        _history_entry("stdout", "line-1", 1),
        _history_entry("stdout", "line-2", 2),
    ]
    broker, follower, generator = _setup(monkeypatch, history)

    frames = await _drain_until_history_end(generator)

    events = [event for event, _ in frames]
    assert events == ["run_status", "historical_logs_start", "log_line", "log_line", "historical_logs_end"]
    assert frames[-1][1]["sent_lines"] == 2
    assert frames[2][1]["data"]["sequence"] == 1

    # 实时帧：sequence 高于历史阈值 → 透传
    broker.publish("run-1", build_log_line_message("run-1", log_type="stdout", content="line-3", timestamp=None, sequence=3, source="realtime"))
    event, data = _parse_frame(await anext(generator))
    assert event == "log_line"
    assert data["data"]["content"] == "line-3"

    await generator.aclose()
    follower.unfollow.assert_awaited_once_with("run-1")
    assert broker.stats()["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_no_history_emits_no_historical_logs(monkeypatch):
    _, _, generator = _setup(monkeypatch, history=[])

    frames = await _drain_until_history_end(generator)

    assert frames[-1][0] == "no_historical_logs"
    assert frames[-1][1]["sent_lines"] == 0
    await generator.aclose()


@pytest.mark.asyncio
async def test_terminal_status_reports_progress_100(monkeypatch):
    _, _, generator = _setup(monkeypatch, history=[], status="success")

    event, data = _parse_frame(await anext(generator))

    assert event == "run_status"
    assert data["data"]["progress"] == 100.0
    assert data["data"]["message"] == "任务执行成功"
    await generator.aclose()


@pytest.mark.asyncio
async def test_overlap_filter_is_keyed_by_log_type_and_accepts_seq_zero(monkeypatch):
    """阈值按 (log_type) 分键；sequence=0 合法（stderr 无历史时阈值 -1）。"""
    history = [_history_entry("stdout", "old", 5)]
    broker, _, generator = _setup(monkeypatch, history)
    await _drain_until_history_end(generator)

    # stdout seq=3 <= 5 → 重叠过滤；stderr seq=0 > -1 → 透传（不能被 stdout 阈值误杀）
    broker.publish("run-1", build_log_line_message("run-1", log_type="stdout", content="dup", timestamp=None, sequence=3, source="realtime"))
    broker.publish("run-1", build_log_line_message("run-1", log_type="stderr", content="err-0", timestamp=None, sequence=0, source="realtime"))

    event, data = _parse_frame(await anext(generator))
    assert event == "log_line"
    assert data["data"]["content"] == "err-0"
    assert data["data"]["sequence"] == 0
    await generator.aclose()


@pytest.mark.asyncio
async def test_frames_without_sequence_pass_through(monkeypatch):
    history = [_history_entry("stdout", "old", 100)]
    broker, _, generator = _setup(monkeypatch, history)
    await _drain_until_history_end(generator)

    broker.publish("run-1", build_log_line_message("run-1", log_type="stdout", content="legacy", timestamp=None, sequence=None, source="realtime"))

    event, data = _parse_frame(await anext(generator))
    assert event == "log_line"
    assert data["data"]["content"] == "legacy"
    await generator.aclose()


@pytest.mark.asyncio
async def test_run_status_messages_are_forwarded(monkeypatch):
    broker, _, generator = _setup(monkeypatch, history=[])
    await _drain_until_history_end(generator)

    broker.publish(
        "run-1",
        {"type": "run_status", "run_id": "run-1", "data": {"status": "success", "progress": 100.0, "message": "done"}},
    )

    event, data = _parse_frame(await anext(generator))
    assert event == "run_status"
    assert data["data"]["status"] == "success"
    await generator.aclose()


@pytest.mark.asyncio
async def test_idle_stream_emits_ping(monkeypatch):
    monkeypatch.setattr(svc_module, "PING_INTERVAL_SECONDS", 0.01)
    _, _, generator = _setup(monkeypatch, history=[])
    await _drain_until_history_end(generator)

    event, data = _parse_frame(await anext(generator))

    assert event == "ping"
    assert data["type"] == "ping"
    await generator.aclose()


@pytest.mark.asyncio
async def test_revoked_session_terminates_stream(monkeypatch):
    monkeypatch.setattr(svc_module, "SESSION_RECHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(svc_module, "_session_still_valid", AsyncMock(return_value=False))
    _, follower, generator = _setup(monkeypatch, history=[])
    await _drain_until_history_end(generator)

    event, data = _parse_frame(await anext(generator))
    assert event == "stream_error"
    assert "会话已失效" in data["message"]

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    follower.unfollow.assert_awaited_once()


@pytest.mark.asyncio
async def test_queue_overflow_terminates_stream(monkeypatch):
    broker, _, generator = _setup(monkeypatch, history=[])
    await _drain_until_history_end(generator)

    subscription = next(iter(broker._subscriptions["run-1"].values()))
    subscription.queue.put_nowait(QUEUE_OVERFLOW)

    event, data = _parse_frame(await anext(generator))
    assert event == "stream_error"
    assert "积压" in data["message"]

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert broker.stats()["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_client_disconnect_releases_subscription(monkeypatch):
    """客户端断连（生成器 aclose）后订阅数归零、跟随计数释放。"""
    broker, follower, generator = _setup(monkeypatch, history=[])
    await _drain_until_history_end(generator)
    assert broker.stats()["total_subscriptions"] == 1

    await generator.aclose()

    assert broker.stats()["total_subscriptions"] == 0
    follower.unfollow.assert_awaited_once_with("run-1")


# --------------------------------------------------------------------------- #
# 端点级：鉴权/权限/容量问题在流开始前以 HTTP 状态码返回
# --------------------------------------------------------------------------- #


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
    broker = RunStreamBroker()
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
        SimpleNamespace(follow=AsyncMock(), unfollow=AsyncMock(), fetch_history=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(svc_module, "run_stream_broker", RunStreamBroker())
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
