"""E2E SSE 日志流客户端的鉴权与解析契约。"""

from typing import Any

import pytest

from tests.e2e import log_client


@pytest.mark.asyncio
async def test_issue_stream_ticket_uses_authenticated_http_endpoint(monkeypatch) -> None:
    calls = []

    async def fake_request_json(client, method, path, *, token=None, **kwargs):
        calls.append((client, method, path, token, kwargs))
        return {"success": True, "data": {"ticket": "one-time-ticket", "ttl": 60}}

    monkeypatch.setattr(log_client, "request_json", fake_request_json)
    client = object()

    ticket = await log_client.issue_stream_ticket(client, "access-jwt", "run-1")

    assert ticket == "one-time-ticket"
    assert calls == [
        (client, "POST", "/logs/stream-ticket", "access-jwt", {"params": {"run_id": "run-1"}}),
    ]


def test_stream_path_contains_only_encoded_ticket() -> None:
    path = log_client.build_stream_path("run-1", "ticket/with?reserved")

    assert path == "/api/v1/logs/runs/run-1/stream?ticket=ticket%2Fwith%3Freserved"
    assert "token=" not in path
    assert "access-jwt" not in path


async def _aiter(items: list[Any]):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_sse_parser_splits_frames_and_skips_comments() -> None:
    lines = [
        ": keep-alive",
        "event: log_line",
        'data: {"type": "log_line", "data": {"content": "hello"}}',
        "",
        "event: ping",
        'data: {"type": "ping"}',
        "",
    ]

    messages = [message async for message in log_client.parse_sse_messages(_aiter(lines))]

    assert messages == [
        {"type": "log_line", "data": {"content": "hello"}},
        {"type": "ping"},
    ]


@pytest.mark.asyncio
async def test_scan_accepts_history_match_when_not_realtime_only() -> None:
    messages = [
        {"type": "historical_logs_start"},
        {"type": "log_line", "data": {"content": "target-log", "source": "pg_history"}},
        {"type": "historical_logs_end"},
        {"type": "log_line", "data": {"content": "target-log", "source": "realtime"}},
    ]

    message = await log_client.scan_for_target_log(
        _aiter(messages),
        "target-log",
        realtime_only=False,
    )

    # 非 realtime_only 模式下历史回放中的命中即可返回（与原 WS 客户端语义一致）
    assert message["data"]["source"] == "pg_history"


@pytest.mark.asyncio
async def test_realtime_scan_ignores_history_and_requires_realtime_source() -> None:
    messages = [
        {"type": "historical_logs_start"},
        {"type": "log_line", "data": {"content": "target-log", "source": "pg_history"}},
        {"type": "historical_logs_end"},
        {"type": "log_line", "data": {"content": "other", "source": "realtime"}},
        {"type": "log_line", "data": {"content": "target-log", "source": "realtime"}},
    ]

    message = await log_client.scan_for_target_log(
        _aiter(messages),
        "target-log",
        realtime_only=True,
    )

    assert message["data"]["source"] == "realtime"
    assert message["data"]["content"] == "target-log"


@pytest.mark.asyncio
async def test_same_stream_scanner_requires_realtime_log_and_terminal_status() -> None:
    messages = [
        {"type": "run_status", "data": {"status": "running"}},
        {"type": "no_historical_logs"},
        {"type": "log_line", "data": {"content": "target-log", "source": "realtime"}},
        {"type": "run_status", "data": {"status": "success"}},
    ]

    log, status = await log_client.scan_for_realtime_log_and_status(
        _aiter(messages),
        "target-log",
        terminal_statuses=frozenset({"success"}),
    )

    assert log["data"]["content"] == "target-log"
    assert status["data"]["status"] == "success"


@pytest.mark.asyncio
async def test_scan_raises_with_observed_types_when_stream_ends() -> None:
    messages = [{"type": "historical_logs_start"}, {"type": "no_historical_logs"}]

    with pytest.raises(AssertionError, match="observed_types"):
        await log_client.scan_for_target_log(_aiter(messages), "missing", realtime_only=False)
