from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_core.application.services.lease_service import LeaseConflictError
from antcode_core.infrastructure.redis import control_group, control_stream
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.control_stream_ownership import (
    ControlAckOutcome,
    ControlPendingEntry,
    ControlPendingOwnerError,
    ack_owned_control_entry,
    control_consumer_name,
    recover_stale_control_entries,
)
from redis.cluster import key_slot

EXPECTED_RECOVERED_ENTRIES = 2
WORKER_ID = "worker-1"
OLD_LEASE_ID = "lease-old"
NEW_LEASE_ID = "lease-new"
WORKER_STREAM = control_stream(WORKER_ID)
CONTROL_GROUP = control_group()


class AckRedis:
    def __init__(
        self,
        owner: str | None,
        *,
        current_lease_id: str,
        lease_switch_to: str | None = None,
        ack_fails: bool = False,
    ) -> None:
        self.owner = owner
        self.current_lease_id = current_lease_id
        self.lease_switch_to = lease_switch_to
        self.ack_fails = ack_fails
        self.eval_calls: list[tuple] = []

    async def get(self, _key):
        return None

    async def xrange(self, _stream, *, min, max, count):
        return [(min, {"control_type": "ping"})]

    async def xpending_range(self, _stream, _group, *, min, max, count):
        owner = self.owner
        if self.lease_switch_to is not None:
            self.current_lease_id = self.lease_switch_to
            self.lease_switch_to = None
        if owner is None:
            return []
        return [{"message_id": min, "consumer": owner}]

    async def eval(self, *args):
        self.eval_calls.append(args)
        _script, _key_count, _stream, _lease_key, _group, _message_id, expected, lease_id, _retention = args
        if self.current_lease_id != lease_id:
            return b"stale_lease"
        if self.owner is None:
            return b"gone"
        if self.owner != expected:
            return b"not_owner"
        if self.ack_fails:
            return b"ack_failed"
        self.owner = None
        return b"acked"


class RecoveryRedis:
    def __init__(self, current_lease_id: str = NEW_LEASE_ID) -> None:
        self.current_lease_id = current_lease_id
        self.calls: list[tuple[str, str, str, str, str]] = []
        self.pages = [
            ("5-0", [("1-0", {"control_type": "ping"})]),
            ("0-0", [("5-0", {"control_type": "cancel"})]),
        ]

    async def xgroup_create(self, *_args, **_kwargs):
        return True

    async def eval(self, *args):
        _script, _key_count, stream, _lease_key, group, consumer, lease_id, _retention, cursor, _count = args
        if lease_id != self.current_lease_id:
            return (b"stale_lease",)
        self.calls.append((stream, group, consumer, lease_id, cursor))
        return self.pages.pop(0)


class WatchRedis:
    def __init__(self) -> None:
        self.consumers: list[str] = []
        self.claim_consumers: list[str] = []
        self.delivered = False

    async def xgroup_create(self, *_args, **_kwargs):
        return True

    async def eval(self, *args):
        consumer = args[5]
        self.claim_consumers.append(consumer)
        return ("0-0", [])

    async def xreadgroup(self, *, consumername, streams, **_kwargs):
        self.consumers.append(consumername)
        stream, cursor = next(iter(streams.items()))
        if cursor != ">" or self.delivered or not stream.endswith("worker-1"):
            return []
        self.delivered = True
        return [(stream, [("1-0", {"control_type": "ping"})])]


def _context() -> MagicMock:
    return MagicMock(abort=AsyncMock())


def _service() -> GatewayControlService:
    lease_store = MagicMock(
        policy=SimpleNamespace(ttl_ms=30_000, renew_after_ms=10_000),
        is_current=AsyncMock(return_value=True),
    )
    return GatewayControlService(lease_store=lease_store)


def _request(lease_id: str = OLD_LEASE_ID) -> control_pb2.AckControlRequest:
    return control_pb2.AckControlRequest(
        worker_id=WORKER_ID,
        event_id=f"{WORKER_STREAM}|1-0",
        success=True,
        lease_id=lease_id,
    )


@pytest.mark.asyncio
async def test_ack_rejects_generation_that_lost_pel_after_lease_check(monkeypatch):
    old_consumer = control_consumer_name(WORKER_ID, OLD_LEASE_ID)
    redis = AckRedis(
        old_consumer,
        current_lease_id=OLD_LEASE_ID,
        lease_switch_to=NEW_LEASE_ID,
    )
    monkeypatch.setattr(control_service_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value=WORKER_ID),
    )
    context = _context()

    response = await _service().AckControl(_request(), context)

    assert response.received is False
    context.abort.assert_awaited_once()
    assert context.abort.await_args.args[0] is grpc.StatusCode.FAILED_PRECONDITION
    assert redis.owner == old_consumer
    assert redis.current_lease_id == NEW_LEASE_ID


@pytest.mark.asyncio
async def test_atomic_ack_rejects_wrong_consumer():
    new_consumer = control_consumer_name(WORKER_ID, NEW_LEASE_ID)
    redis = AckRedis(new_consumer, current_lease_id=OLD_LEASE_ID)
    entry = ControlPendingEntry(
        WORKER_ID,
        OLD_LEASE_ID,
        WORKER_STREAM,
        CONTROL_GROUP,
        "1-0",
        control_consumer_name(WORKER_ID, OLD_LEASE_ID),
    )

    with pytest.raises(ControlPendingOwnerError, match="租约代际"):
        await ack_owned_control_entry(redis, entry)

    assert redis.owner == new_consumer


@pytest.mark.asyncio
async def test_atomic_ack_reports_missing_pel_as_not_received():
    redis = AckRedis(None, current_lease_id=OLD_LEASE_ID)
    entry = ControlPendingEntry(
        WORKER_ID,
        OLD_LEASE_ID,
        WORKER_STREAM,
        CONTROL_GROUP,
        "1-0",
        control_consumer_name(WORKER_ID, OLD_LEASE_ID),
    )

    outcome = await ack_owned_control_entry(redis, entry)

    assert outcome is ControlAckOutcome.GONE


@pytest.mark.asyncio
async def test_atomic_ack_exposes_redis_xack_false():
    consumer = control_consumer_name(WORKER_ID, OLD_LEASE_ID)
    redis = AckRedis(consumer, current_lease_id=OLD_LEASE_ID, ack_fails=True)
    entry = ControlPendingEntry(WORKER_ID, OLD_LEASE_ID, WORKER_STREAM, CONTROL_GROUP, "1-0", consumer)

    with pytest.raises(RuntimeError, match="XACK 未确认"):
        await ack_owned_control_entry(redis, entry)

    assert redis.owner == consumer


@pytest.mark.asyncio
async def test_old_generation_pel_is_recovered_into_current_consumer():
    redis = RecoveryRedis()
    channel = SimpleNamespace(stream_key=WORKER_STREAM, group=CONTROL_GROUP)
    consumer = control_consumer_name(WORKER_ID, NEW_LEASE_ID)

    claimed = await recover_stale_control_entries(
        redis,
        channels=(channel,),
        worker_id=WORKER_ID,
        lease_id=NEW_LEASE_ID,
    )

    assert len(claimed) == EXPECTED_RECOVERED_ENTRIES
    assert [entry.message_id for entry in claimed] == ["1-0", "5-0"]
    assert [call[2] for call in redis.calls] == [consumer, consumer]
    assert [call[4] for call in redis.calls] == ["-", "5-0"]
    assert {call[3] for call in redis.calls} == {NEW_LEASE_ID}


def test_control_stream_and_lease_fence_share_cluster_slot():
    consumer = control_consumer_name(WORKER_ID, OLD_LEASE_ID)
    entry = ControlPendingEntry(WORKER_ID, OLD_LEASE_ID, WORKER_STREAM, CONTROL_GROUP, "1-0", consumer)

    assert key_slot(entry.stream_key.encode()) == key_slot(entry.lease_key.encode())


@pytest.mark.asyncio
async def test_stale_watch_cannot_claim_current_generation_pel():
    redis = RecoveryRedis(current_lease_id=NEW_LEASE_ID)
    channel = SimpleNamespace(stream_key=WORKER_STREAM, group=CONTROL_GROUP)

    with pytest.raises(LeaseConflictError, match="PEL 接管时 lease 已切代"):
        await recover_stale_control_entries(
            redis,
            channels=(channel,),
            worker_id=WORKER_ID,
            lease_id=OLD_LEASE_ID,
        )

    assert redis.calls == []


@pytest.mark.asyncio
async def test_watch_control_reads_and_recovers_with_lease_consumer(monkeypatch):
    redis = WatchRedis()
    monkeypatch.setattr(control_service_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value=WORKER_ID),
    )
    context = MagicMock(
        abort=AsyncMock(),
        cancelled=MagicMock(return_value=False),
        send_initial_metadata=AsyncMock(),
    )

    event = await anext(
        _service().WatchControl(
            control_pb2.WatchControlRequest(worker_id=WORKER_ID, lease_id=OLD_LEASE_ID),
            context,
        )
    )

    expected = control_consumer_name(WORKER_ID, OLD_LEASE_ID)
    assert event.event_id == f"{WORKER_STREAM}|1-0"
    assert set(redis.claim_consumers) == {expected}
    assert set(redis.consumers) == {expected}


@pytest.mark.asyncio
async def test_watch_maps_recovery_lease_loss_to_failed_precondition(monkeypatch):
    redis = RecoveryRedis(current_lease_id=NEW_LEASE_ID)
    monkeypatch.setattr(control_service_module, "get_redis_client", AsyncMock(return_value=redis))
    auth = AsyncMock(return_value=WORKER_ID)
    monkeypatch.setattr(control_service_module, "require_authenticated_worker", auth)
    context = _context()

    with pytest.raises(StopAsyncIteration):
        await anext(
            _service().WatchControl(
                control_pb2.WatchControlRequest(worker_id=WORKER_ID, lease_id=OLD_LEASE_ID),
                context,
            )
        )

    assert context.abort.await_args.args[0] is grpc.StatusCode.FAILED_PRECONDITION
