"""Shared Redis SSE event stream tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.infrastructure.redis.sse_event_stream import (
    _APPEND_EVENT_SCRIPT,
    SSE_EVENT_STREAM_MAX_BYTES,
    SSE_EVENT_STREAM_MAXLEN,
    SSEEventPublishError,
    decode_sse_event,
    publish_sse_event,
)

SSE_EVENT_SCRIPT_KEY_COUNT = 4


def test_accounting_recovery_preserves_stream_id_monotonicity() -> None:
    assert "not total_raw" in _APPEND_EVENT_SCRIPT
    assert "oldest_size <= 0" in _APPEND_EVENT_SCRIPT
    assert "SSE_MAX_BYTES_INVALID" in _APPEND_EVENT_SCRIPT
    assert "redis.call('XTRIM', KEYS[1], 'MAXLEN', 0)" in _APPEND_EVENT_SCRIPT
    assert "redis.call('DEL', KEYS[1]" not in _APPEND_EVENT_SCRIPT


@pytest.mark.asyncio
async def test_publish_writes_bounded_decodable_event():
    redis = SimpleNamespace(eval=AsyncMock(return_value=["1-0", 10, 0, 1]))
    message = {
        "type": "run_status",
        "run_id": "run-1",
        "data": {"status": "success"},
    }

    await publish_sse_event(message, redis=redis)

    args = redis.eval.await_args.args
    payload = args[-4]
    assert decode_sse_event({"payload": payload}) == message
    assert args[-3:] == (len(payload.encode("utf-8")), SSE_EVENT_STREAM_MAX_BYTES, SSE_EVENT_STREAM_MAXLEN)
    keys = args[2:6]
    assert len(keys) == SSE_EVENT_SCRIPT_KEY_COUNT
    assert len({key.split("}", 1)[0] for key in keys}) == 1


@pytest.mark.asyncio
async def test_publish_reports_accounting_recovery(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=["1-0", 10, 1, 1]))
    warning = MagicMock()
    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.sse_event_stream.logger.warning",
        warning,
    )

    await publish_sse_event({"type": "run_status"}, redis=redis)

    warning.assert_called_once()


@pytest.mark.asyncio
async def test_publish_wraps_redis_failure_for_pel_policy():
    redis = SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError("down")))

    with pytest.raises(SSEEventPublishError) as exc_info:
        await publish_sse_event({"type": "run_status"}, redis=redis)

    assert isinstance(exc_info.value.__cause__, ConnectionError)


@pytest.mark.asyncio
async def test_explicit_missing_client_fails_without_hidden_retry():
    with pytest.raises(SSEEventPublishError) as exc_info:
        await publish_sse_event({"type": "run_status"}, redis=None)

    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_decode_rejects_nonstandard_json_constants(constant):
    with pytest.raises(ValueError, match="非法 JSON 常量"):
        decode_sse_event({"payload": '{"value":' + constant + "}"})


@pytest.mark.asyncio
async def test_publish_rejects_oversized_event_before_redis_write(monkeypatch):
    monkeypatch.setattr("antcode_core.infrastructure.redis.sse_event_stream.SSE_EVENT_MAX_BYTES", 32)
    redis = SimpleNamespace(eval=AsyncMock())

    with pytest.raises(SSEEventPublishError, match="超过"):
        await publish_sse_event({"data": {"content": "x" * 64}}, redis=redis)

    redis.eval.assert_not_awaited()
