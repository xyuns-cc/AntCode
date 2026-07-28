"""SSE capacity races after HTTP preflight remain explicit in the event stream."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.log_stream_service as service_module
import pytest
from antcode_web_api.streams.log_stream_service import LogStreamService
from antcode_web_api.streams.run_stream_broker import StreamLimitExceededError


def _frame_payload(frame: bytes) -> tuple[str, dict]:
    lines = frame.decode("utf-8").splitlines()
    event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
    data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
    return event, json.loads(data)


@pytest.mark.asyncio
async def test_subscribe_capacity_race_emits_limit_error(monkeypatch) -> None:
    broker = SimpleNamespace(
        subscribe=AsyncMock(side_effect=StreamLimitExceededError("日志流连接数已达上限")),
    )
    monkeypatch.setattr(service_module, "run_stream_broker", broker)
    execution = SimpleNamespace(status=SimpleNamespace(value="running"))
    stream = LogStreamService().stream(
        "run-1",
        execution,
        user_id=7,
        session_jti="jti",
    )

    event, payload = _frame_payload(await anext(stream))

    assert event == "stream_error"
    assert payload["code"] == "limit"
    assert payload["message"] == "日志流连接数已达上限"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
