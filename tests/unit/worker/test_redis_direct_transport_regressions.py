"""Focused regressions for Direct Redis control and requeue boundaries."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.infrastructure.redis.control_plane import (
    build_config_update_control_payload,
    control_stream,
)
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.base import GenerationLostError
from antcode_worker.transport.redis.transport import RedisTransport
from redis.exceptions import ConnectionError as RedisConnectionError

redis_transport_module = sys.modules[RedisTransport.__module__]
_REQUEST_ID = f"worker-1:{'a' * 32}"
_REPLY_STREAM = f"antcode:control:reply:{_REQUEST_ID}"
_FOREIGN_REQUEST_ID = f"worker-2:{'b' * 32}"
_MEMORY_LIMIT_MB = 768
_CPU_LIMIT_SECONDS = 60
_CONTROL_STREAM = control_stream("worker-1")


def test_direct_config_update_unwraps_nested_config_payload():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    encoded = build_config_update_control_payload({"max_concurrent_tasks": 8, "task_memory_limit_mb": 512})

    message = transport._build_control_message(_CONTROL_STREAM, "1-0", encoded)

    assert message.payload == {"max_concurrent_tasks": 8, "task_memory_limit_mb": 512}


@pytest.mark.asyncio
async def test_config_update_producer_direct_decode_and_engine_dispatch():
    direct = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    encoded = build_config_update_control_payload(
        {
            "task_memory_limit_mb": _MEMORY_LIMIT_MB,
            "task_cpu_time_limit_sec": _CPU_LIMIT_SECONDS,
        }
    )
    message = direct._build_control_message(_CONTROL_STREAM, "1-0", encoded)
    transport = MagicMock()
    transport.ack_control = AsyncMock(return_value=True)
    engine = Engine(transport=transport, executor=MagicMock(), max_concurrent=1)

    await engine._dispatch_control(message)

    assert engine._policies.resource.memory_limit_mb == _MEMORY_LIMIT_MB
    assert engine._policies.resource.cpu_limit_seconds == _CPU_LIMIT_SECONDS
    transport.ack_control.assert_awaited_once_with(f"{_CONTROL_STREAM}|1-0")


@pytest.mark.parametrize(
    "fields",
    [
        {
            "control_type": "runtime_manage",
            "action": "list_envs",
            "request_id": _FOREIGN_REQUEST_ID,
            "reply_stream": f"antcode:control:reply:{_FOREIGN_REQUEST_ID}",
            "payload": "{}",
            "expires_at_ms": "2000000",
        },
        {
            "control_type": "runtime_manage",
            "action": "list_envs",
            "request_id": _REQUEST_ID,
            "reply_stream": "antcode:control:reply:forged",
            "payload": "{}",
            "expires_at_ms": "2000000",
        },
        {
            "control_type": "runtime_manage",
            "action": "list_envs",
            "request_id": _REQUEST_ID,
            "reply_stream": _REPLY_STREAM,
            "payload": "[]",
            "expires_at_ms": "2000000",
        },
        {
            "control_type": "runtime_manage",
            "request_id": _REQUEST_ID,
            "reply_stream": _REPLY_STREAM,
            "payload": "{}",
            "expires_at_ms": "2000000",
        },
        {
            "control_type": "runtime_manage",
            "action": "list_envs",
            "request_id": _REQUEST_ID,
            "reply_stream": _REPLY_STREAM,
            "payload": "{}",
        },
        {
            "control_type": "runtime_manage",
            "action": "list_envs",
            "request_id": _REQUEST_ID,
            "reply_stream": _REPLY_STREAM,
            "payload": "{}",
            "expires_at_ms": "invalid",
        },
    ],
)
@pytest.mark.asyncio
async def test_poll_control_quarantines_semantically_invalid_runtime_request(monkeypatch, fields):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    redis = AsyncMock()
    redis.eval.return_value = 1
    transport._redis = redis
    transport._lease_store = SimpleNamespace(lease_key=lambda _worker_id: "antcode:lease:worker-1")
    monkeypatch.setattr(redis_transport_module, "recover_runtime_control_settlement", AsyncMock(return_value=False))

    message = await transport._decode_control_delivery(_CONTROL_STREAM, "2-0", fields)

    assert message is None
    redis.eval.assert_awaited_once()


def _valid_runtime_request() -> dict[str, str]:
    return {
        "control_type": "runtime_manage",
        "action": "list_envs",
        "request_id": _REQUEST_ID,
        "reply_stream": _REPLY_STREAM,
        "payload": "{}",
        "expires_at_ms": "2000000",
    }


def _connected_runtime_transport() -> tuple[RedisTransport, AsyncMock]:
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    redis = AsyncMock()
    redis.eval.return_value = 1
    transport._redis = redis
    transport._lease_store = SimpleNamespace(lease_key=lambda _worker_id: "antcode:lease:worker-1")
    return transport, redis


@pytest.mark.asyncio
async def test_poll_control_quarantines_invalid_runtime_recovery_evidence(monkeypatch):
    transport, redis = _connected_runtime_transport()
    monkeypatch.setattr(
        redis_transport_module,
        "recover_runtime_control_settlement",
        AsyncMock(side_effect=ValueError("corrupt source")),
    )

    message = await transport._decode_control_delivery(_CONTROL_STREAM, "2-0", _valid_runtime_request())

    assert message is None
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_control_preserves_runtime_request_on_redis_failure(monkeypatch):
    transport, redis = _connected_runtime_transport()
    monkeypatch.setattr(
        redis_transport_module,
        "recover_runtime_control_settlement",
        AsyncMock(side_effect=RedisConnectionError("redis unavailable")),
    )

    with pytest.raises(RedisConnectionError, match="redis unavailable"):
        await transport._decode_control_delivery(_CONTROL_STREAM, "2-0", _valid_runtime_request())

    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_defer_task_propagates_generation_loss(monkeypatch):
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    monkeypatch.setattr(
        transport,
        "_require_current_generation",
        AsyncMock(side_effect=GenerationLostError("superseded")),
    )

    with pytest.raises(GenerationLostError, match="superseded"):
        await transport.defer_task("{antcode}:task:ready:worker-1|1-0")


@pytest.mark.asyncio
async def test_requeue_task_dead_letters_negative_requeue_count():
    transport = RedisTransport(redis_url="redis://localhost:6379/0", worker_id="worker-1")
    transport._running = True
    redis = AsyncMock()
    transport._redis = redis
    dead_letter_owned = AsyncMock()
    transport._reclaimer = MagicMock(dead_letter_owned=dead_letter_owned)
    receipt = "{antcode}:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = (
        "{antcode}:task:ready:worker-1",
        "1-0",
        {"task_id": "task-1", "requeue_count": "-100"},
    )

    assert await transport.requeue_task(receipt, reason="invalid") is True

    dead_letter_owned.assert_awaited_once()
    assert "非负整数" in dead_letter_owned.await_args.args[2]["_bad_frame_error"]
    redis.eval.assert_not_awaited()
    assert receipt not in transport._receipt_cache
