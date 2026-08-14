from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2
from antcode_core.application.services.lease_service import LeaseConflictError, LeaseRevokedError
from antcode_core.infrastructure.redis import (
    control_global_group,
    control_global_stream,
    control_group,
    control_stream,
)
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services import runtime_control_expiry
from antcode_gateway.services.control_event_builders import build_control_event
from antcode_gateway.services.control_service import (
    CONTROL_STREAM_MAXLEN,
    GatewayControlService,
    _ControlChannel,
    _ControlWatchSession,
)


class PendingRedis:
    def __init__(self, total: int) -> None:
        self.entries = [(f"{index}-0", {"control_type": "ping"}) for index in range(1, total + 1)]
        self.calls: list[str] = []
        self.groups: list[str] = []

    async def xreadgroup(self, *, groupname, streams, count, **_kwargs):
        stream_key, cursor = next(iter(streams.items()))
        self.calls.append(cursor)
        self.groups.append(groupname)
        cursor_index = int(cursor.split("-", 1)[0])
        page = self.entries[cursor_index : cursor_index + count]
        return [] if not page else [(stream_key, page)]


class MissingGroupRedis:
    def __init__(self) -> None:
        self.reads = 0
        self.created: list[tuple[str, str]] = []

    async def xreadgroup(self, **_kwargs):
        self.reads += 1
        if self.reads == 1:
            raise RuntimeError("NOGROUP No such key or consumer group")
        return []

    async def xgroup_create(self, stream_key, group, **_kwargs):
        self.created.append((stream_key, group))


@pytest.mark.asyncio
async def test_pending_control_recovery_pages_past_stream_maxlen():
    redis = PendingRedis(CONTROL_STREAM_MAXLEN + 1)
    lease_store = MagicMock(is_current=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)
    context = MagicMock()
    session = _ControlWatchSession(
        context=context,
        worker_id="worker-1",
        lease_id="lease-1",
        consumer="worker-1:lease-1",
        channels=(_ControlChannel(control_stream("worker-1"), "antcode-control"),),
    )

    events = [event async for event in service._replay_pending_control(redis, session)]

    assert len(events) == CONTROL_STREAM_MAXLEN + 1
    assert len({event.event_id for event in events}) == len(events)
    assert redis.calls == ["0-0", f"{CONTROL_STREAM_MAXLEN}-0"]
    assert redis.groups == ["antcode-control", "antcode-control"]


@pytest.mark.asyncio
async def test_stream_new_control_reraises_cancellation():
    # C5 回归：取消时应向上抛出 CancelledError，而非吞成干净 EOF。
    class CancellingRedis:
        eval = AsyncMock(return_value=("0-0", []))

        async def xreadgroup(self, **_kwargs):
            raise asyncio.CancelledError

    lease_store = MagicMock(is_current=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)
    session = _ControlWatchSession(
        context=MagicMock(cancelled=MagicMock(return_value=False)),
        worker_id="worker-1",
        lease_id="lease-1",
        consumer="worker-1:lease-1",
        channels=(_ControlChannel(control_stream("worker-1"), control_group()),),
    )

    generator = service._stream_new_control(CancellingRedis(), session)
    with pytest.raises(asyncio.CancelledError):
        await anext(generator)


@pytest.mark.asyncio
async def test_global_pending_replay_uses_worker_broadcast_group():
    redis = PendingRedis(1)
    lease_store = MagicMock(is_current=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)
    session = _ControlWatchSession(
        context=MagicMock(),
        worker_id="worker-1",
        lease_id="lease-1",
        consumer="worker-1:lease-1",
        channels=(_ControlChannel(control_global_stream(), control_global_group("worker-1")),),
    )

    events = [event async for event in service._replay_pending_control(redis, session)]

    assert [event.event_id for event in events] == [f"{control_global_stream()}|1-0"]
    assert redis.groups == [control_global_group("worker-1")]


@pytest.mark.asyncio
async def test_control_read_recreates_group_after_redis_group_loss():
    redis = MissingGroupRedis()
    service = GatewayControlService(lease_store=MagicMock())
    channel = _ControlChannel(control_stream("worker-1"), control_group())
    service._initialized_control_groups.add((channel.stream_key, channel.group))
    session = _ControlWatchSession(
        context=MagicMock(),
        worker_id="worker-1",
        lease_id="lease-1",
        consumer="worker-1:lease-1",
        channels=(channel,),
    )

    result = await service._read_control_group(
        redis,
        session=session,
        channel=channel,
        cursor=">",
        count=1,
        block=1,
    )

    assert result == []
    assert redis.reads == 2
    assert redis.created == [(channel.stream_key, channel.group)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        LeaseRevokedError("worker-1", "lease-old"),
        LeaseConflictError("worker-1", "lease-old"),
    ],
)
async def test_fenced_lease_is_returned_as_protocol_response(monkeypatch, failure):
    lease_handler = MagicMock(handle=AsyncMock())
    lease_store = MagicMock(
        policy=SimpleNamespace(ttl_ms=30_000, renew_after_ms=10_000),
        grant=AsyncMock(side_effect=failure),
    )
    authorizer = AsyncMock(return_value=SimpleNamespace(allowed=True))
    service = GatewayControlService(lease_handler, lease_store, lease_authorizer=authorizer)
    context = MagicMock(abort=AsyncMock())
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-1"),
    )

    response = await service.Lease(
        control_pb2.LeaseRequest(worker_id="worker-1", current_lease_id="lease-old"),
        context,
    )

    assert response.revoked is True
    assert response.lease_id == ""
    assert response.renew_after_ms == 10_000
    assert response.revoke_reason
    lease_handler.handle.assert_not_awaited()
    context.abort.assert_not_awaited()


def test_runtime_control_event_preserves_execution_deadline():
    event = build_control_event(
        stream_key=control_stream("worker-1"),
        msg_id="1-0",
        data={
            "control_type": "runtime_manage",
            "request_id": "request-1",
            "action": "list_envs",
            "reply_stream": "antcode:control:reply:request-1",
            "payload": "{}",
            "expires_at_ms": "4102444800000",
        },
    )

    assert event is not None
    assert event.runtime_control.expires_at_ms == 4_102_444_800_000


@pytest.mark.asyncio
async def test_invalid_runtime_deadline_is_acked_as_poison():
    redis = MagicMock(eval=AsyncMock(return_value=b"acked"))
    lease_store = MagicMock(is_current=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)
    session = _ControlWatchSession(
        context=MagicMock(),
        worker_id="worker-1",
        lease_id="lease-1",
        consumer="worker-1:lease-1",
        channels=(_ControlChannel(control_stream("worker-1"), "antcode-control"),),
    )

    event = await service._decode_control_event(
        redis,
        session,
        stream_key=control_stream("worker-1"),
        message_id="2-0",
        data={
            "control_type": "runtime_manage",
            "request_id": "request-1",
            "action": "list_envs",
            "reply_stream": "antcode:control:reply:request-1",
            "payload": "{}",
            "expires_at_ms": "not-an-integer",
        },
    )

    assert event is None
    assert redis.eval.await_args.args[6] == "worker-1:lease-1"


@pytest.mark.asyncio
async def test_global_runtime_control_is_logged_and_acked_by_worker_group(monkeypatch):
    redis = MagicMock(eval=AsyncMock(return_value=b"acked"))
    lease_store = MagicMock(is_current=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)
    session = _broadcast_session("worker-1")
    observed_logger = MagicMock()  # runtime_manage 构建已拆到 runtime_control_expiry
    monkeypatch.setattr(runtime_control_expiry, "logger", observed_logger)

    event = await service._decode_control_event(
        redis,
        session,
        stream_key=control_global_stream(),
        message_id="3-0",
        data={
            "control_type": "runtime_manage",
            "request_id": "request-global",
            "action": "list_envs",
            "reply_stream": "antcode:control:reply:request-global",
            "payload": "{}",
            "expires_at_ms": "4102444800000",
        },
    )

    assert event is None
    observed_logger.error.assert_called_once_with(
        "拒绝 global runtime_manage: stream={} msg_id={} request_id={}",
        control_global_stream(),
        "3-0",
        "request-global",
    )
    assert redis.eval.await_args.args[6] == "worker-1:lease-worker-1"


class BroadcastRedis:
    def __init__(self) -> None:
        self.delivered_groups: set[str] = set()
        self.eval = AsyncMock(return_value=("0-0", []))

    async def xreadgroup(self, *, groupname, streams, **_kwargs):
        stream_key = next(iter(streams))
        if stream_key != control_global_stream() or groupname in self.delivered_groups:
            return []
        self.delivered_groups.add(groupname)
        return [(stream_key, [("10-0", {"control_type": "ping"})])]


def _broadcast_session(worker_id: str) -> _ControlWatchSession:
    return _ControlWatchSession(
        context=MagicMock(cancelled=MagicMock(return_value=False)),
        worker_id=worker_id,
        lease_id=f"lease-{worker_id}",
        consumer=f"{worker_id}:lease-{worker_id}",
        channels=(
            _ControlChannel(control_stream(worker_id), control_group()),
            _ControlChannel(control_global_stream(), control_global_group(worker_id)),
        ),
    )


@pytest.mark.asyncio
async def test_global_control_is_delivered_once_per_worker_group():
    redis = BroadcastRedis()
    lease_store = MagicMock(is_current=AsyncMock(return_value=True))
    service = GatewayControlService(lease_store=lease_store)

    first = await anext(service._stream_new_control(redis, _broadcast_session("worker-1")))
    second = await anext(service._stream_new_control(redis, _broadcast_session("worker-2")))

    assert first.event_id == second.event_id == f"{control_global_stream()}|10-0"
    assert redis.delivered_groups == {
        control_global_group("worker-1"),
        control_global_group("worker-2"),
    }
