"""SSE 会话重校验、积压与资源释放。"""

from unittest.mock import AsyncMock

import antcode_web_api.streams.log_stream_service as svc_module
import pytest
from antcode_web_api.streams.run_stream_broker import QUEUE_OVERFLOW

from tests.unit.web_api.log_stream_test_support import (
    drain_until_history_end,
    parse_frame,
    setup_stream,
)


@pytest.mark.asyncio
async def test_revoked_session_terminates_stream(monkeypatch):
    monkeypatch.setattr(svc_module, "SESSION_RECHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(svc_module, "_session_still_valid", AsyncMock(return_value=False))
    _, follower, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)

    event, data = parse_frame(await anext(generator))
    assert event == "stream_error"
    assert data["code"] == "session_revoked"
    assert "会话已失效" in data["message"]

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    follower.unfollow.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_recheck_fails_closed_on_db_error(monkeypatch):
    """PG 故障时终止受保护日志流，不能继续暴露撤销后的会话数据。"""
    monkeypatch.setattr(svc_module, "SESSION_RECHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        svc_module,
        "_session_still_valid",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    _, _, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)

    event, data = parse_frame(await anext(generator))

    assert event == "stream_error"
    assert data["code"] == "session_check_failed"
    with pytest.raises(StopAsyncIteration):
        await anext(generator)


@pytest.mark.asyncio
async def test_queue_overflow_terminates_stream(monkeypatch):
    broker, _, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)

    subscription = next(iter(broker._subscriptions["run-1"].values()))
    subscription.queue.put_nowait(QUEUE_OVERFLOW)

    event, data = parse_frame(await anext(generator))
    assert event == "stream_error"
    assert data["code"] == "overflow"
    assert "积压" in data["message"]

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert (await broker.stats())["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_client_disconnect_releases_subscription(monkeypatch):
    """客户端断连（生成器 aclose）后订阅数归零、跟随计数释放。"""
    broker, follower, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)
    assert (await broker.stats())["total_subscriptions"] == 1

    await generator.aclose()

    assert (await broker.stats())["total_subscriptions"] == 0
    follower.unfollow.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_capacity_release_failure_still_releases_follower(monkeypatch):
    broker, follower, generator = setup_stream(monkeypatch, history=[])
    await drain_until_history_end(generator)
    broker._capacity_limiter.release_error = RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await generator.aclose()

    assert broker.has_subscribers("run-1") is False
    follower.unfollow.assert_awaited_once_with("run-1")
