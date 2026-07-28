from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_core.infrastructure.redis import control_group, control_reply_stream, control_stream
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import (
    GatewayControlService,
    _ControlChannel,
    _ControlWatchSession,
)
from antcode_gateway.services.runtime_control_recovery import recover_committed_runtime_result
from antcode_gateway.services.runtime_control_result import (
    CONTROL_SETTLEMENT_TTL_SECONDS,
)
from antcode_gateway.services.runtime_control_settlement_store import settlement_key
from redis.exceptions import ResponseError


class _Pipeline:
    def __init__(self, redis: RecoveryRedis) -> None:
        self.redis = redis
        self.commands: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def xadd(self, stream, payload, **kwargs):
        self.commands.append((stream, payload, kwargs))

    def expire(self, stream, ttl):
        self.commands.append((stream, ttl))

    async def execute(self):
        stream, payload, kwargs = self.commands[0]
        message_id = kwargs["id"]
        if (stream, message_id) in self.redis.entries:
            raise ResponseError("equal or smaller stream ID")
        self.redis.entries[(stream, message_id)] = dict(payload)
        self.redis.operations.extend(["xadd", "expire"])
        return [message_id, True]


class RecoveryRedis:
    def __init__(self) -> None:
        self.stream = control_stream("worker-1")
        self.message_id = "1-0"
        self.source = {
            "control_type": "runtime_manage",
            "request_id": "request-1",
            "reply_stream": control_reply_stream("request-1"),
            "action": "list_envs",
            "payload": "{}",
            "expires_at_ms": "4102444800000",
        }
        self.entries: dict[tuple[str, str], dict] = {(self.stream, self.message_id): self.source}
        self.values: dict[str, str] = {}
        self.set_ttls: dict[str, int] = {}
        self.operations: list[str] = []
        self.ack_requests: list[tuple[str, str, str]] = []
        self.fail_next_ack = False
        self.current_lease_id = "lease-old"
        self.read_count = 0

    async def get(self, key):
        self.operations.append("get")
        return self.values.get(key)

    async def set(self, key, value, *, ex, nx):
        self.operations.append("set")
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.set_ttls[key] = ex
        return True

    async def eval(self, *_args):
        _, _, _, marker_key, lease_id, marker, fingerprint, ttl_ms = _args
        self.operations.append("set")
        if self.current_lease_id != lease_id:
            return [-2, ""]
        stored = self.values.get(marker_key)
        if stored is not None and stored not in {marker, fingerprint}:
            return [-1, stored]
        self.values[marker_key] = stored or marker
        self.set_ttls[marker_key] = ttl_ms // 1000
        return [0 if stored else 1, self.values[marker_key]]

    async def xrange(self, stream, *, min, max, count):
        self.operations.append("xrange")
        payload = self.entries.get((stream, min))
        return [] if payload is None else [(min, payload)]

    async def xpending_range(self, stream, group, *, min, max, count, consumername=None):
        self.operations.append("xpending")
        return [{"message_id": min, "consumer": "worker-1"}]

    async def xreadgroup(self, *, groupname, consumername, streams, **_kwargs):
        self.read_count += 1
        if self.read_count > 1:
            return []
        return [(next(iter(streams)), [(self.message_id, self.source)])]

    def pipeline(self, *, transaction):
        assert transaction is True
        return _Pipeline(self)

    async def expire(self, stream, ttl):
        self.operations.append("expire")
        return True

    async def xack(self, stream, group, message_id):
        self.operations.append("xack")
        self.ack_requests.append((stream, group, message_id))
        if self.fail_next_ack:
            self.fail_next_ack = False
            raise RuntimeError("response lost after commit")
        return 1


def _request() -> control_pb2.AckControlRequest:
    return control_pb2.AckControlRequest(
        worker_id="worker-1",
        event_id=f"{control_stream('worker-1')}|1-0",
        success=True,
        request_id="request-1",
        data_json='{"envs":["shared-py312"]}',
        lease_id="lease-old",
    )


def _context() -> MagicMock:
    return MagicMock(abort=AsyncMock())


def _service() -> GatewayControlService:
    return GatewayControlService(lease_store=MagicMock(is_current=AsyncMock(return_value=True)))


def _session() -> _ControlWatchSession:
    return _ControlWatchSession(
        context=MagicMock(),
        worker_id="worker-1",
        lease_id="lease-new",
        consumer="worker-1",
        channels=(_ControlChannel(control_stream("worker-1"), control_group()),),
    )


def _install(monkeypatch, redis: RecoveryRedis) -> None:
    monkeypatch.setattr(control_service_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(control_service_module, "require_authenticated_worker", AsyncMock(return_value="worker-1"))
    monkeypatch.setattr(control_service_module, "trim_acknowledged_stream", AsyncMock())


async def _commit_before_lost_ack(monkeypatch) -> RecoveryRedis:
    redis = RecoveryRedis()
    redis.fail_next_ack = True
    _install(monkeypatch, redis)
    response = await _service().AckControl(_request(), _context())
    assert response.received is False
    marker_key = settlement_key(_request().event_id)
    assert marker_key in redis.values
    assert redis.set_ttls[marker_key] == CONTROL_SETTLEMENT_TTL_SECONDS
    assert (control_reply_stream("request-1"), "1-0") in redis.entries
    return redis


@pytest.mark.asyncio
async def test_new_lease_watch_settles_committed_pending_without_redelivery(monkeypatch):
    redis = await _commit_before_lost_ack(monkeypatch)

    events = [event async for event in _service()._replay_pending_control(redis, _session())]

    assert events == []
    assert redis.ack_requests[-1] == (control_stream("worker-1"), control_group(), "1-0")
    control_service_module.trim_acknowledged_stream.assert_awaited_once_with(
        redis,
        control_stream("worker-1"),
        control_group(),
    )


@pytest.mark.asyncio
async def test_evidence_repairs_missing_reply_before_xack(monkeypatch):
    redis = await _commit_before_lost_ack(monkeypatch)
    reply_key = (control_reply_stream("request-1"), "1-0")
    redis.entries.pop(reply_key)

    event = await _service()._decode_control_event(
        redis,
        _session(),
        stream_key=redis.stream,
        message_id=redis.message_id,
        data=redis.source,
    )

    assert event is None
    assert redis.entries[reply_key]["request_id"] == "request-1"
    assert redis.operations[-1] == "xack"


@pytest.mark.asyncio
async def test_marker_cas_rejects_lease_switch_after_precheck(monkeypatch):
    redis = RecoveryRedis()
    redis.current_lease_id = "lease-new"
    _install(monkeypatch, redis)
    context = _context()

    response = await _service().AckControl(_request(), context)

    assert response.received is False
    context.abort.assert_awaited_once()
    assert context.abort.await_args.args[0] == grpc.StatusCode.FAILED_PRECONDITION
    assert settlement_key(_request().event_id) not in redis.values
    assert (control_reply_stream("request-1"), "1-0") not in redis.entries
    assert "xack" not in redis.operations


@pytest.mark.asyncio
async def test_legacy_marker_requires_matching_persisted_reply(monkeypatch):
    redis = await _commit_before_lost_ack(monkeypatch)
    marker_key = settlement_key(_request().event_id)
    reply_key = (control_reply_stream("request-1"), "1-0")
    fingerprint = redis.entries[reply_key]["settlement"]
    redis.values[marker_key] = fingerprint

    assert await recover_committed_runtime_result(
        redis,
        event_id=_request().event_id,
        worker_id="worker-1",
        message_id="1-0",
        raw_event=redis.source,
    )
    redis.entries.pop(reply_key)
    with pytest.raises(RuntimeError, match="marker 存在但 reply 缺失"):
        await recover_committed_runtime_result(
            redis,
            event_id=_request().event_id,
            worker_id="worker-1",
            message_id="1-0",
            raw_event=redis.source,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["event_id", "worker_id", "request_id"])
async def test_recovery_rejects_mismatched_evidence_identity(monkeypatch, field):
    redis = await _commit_before_lost_ack(monkeypatch)
    marker_key = settlement_key(_request().event_id)
    evidence = json.loads(redis.values[marker_key])
    evidence[field] = f"wrong-{field}"
    redis.values[marker_key] = json.dumps(evidence, separators=(",", ":"), sort_keys=True)

    with pytest.raises(ValueError, match="身份不匹配"):
        await recover_committed_runtime_result(
            redis,
            event_id=_request().event_id,
            worker_id="worker-1",
            message_id="1-0",
            raw_event=redis.source,
        )


@pytest.mark.asyncio
async def test_recovery_rejects_evidence_with_invalid_fingerprint(monkeypatch):
    redis = await _commit_before_lost_ack(monkeypatch)
    marker_key = settlement_key(_request().event_id)
    evidence = json.loads(redis.values[marker_key])
    evidence["fingerprint"] = "0" * 64
    redis.values[marker_key] = json.dumps(evidence, separators=(",", ":"), sort_keys=True)

    with pytest.raises(ValueError, match="fingerprint 无效"):
        await recover_committed_runtime_result(
            redis,
            event_id=_request().event_id,
            worker_id="worker-1",
            message_id="1-0",
            raw_event=redis.source,
        )
