from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from antcode_contracts import control_pb2
from antcode_core.application.services.lease_service import LeaseConflictError
from antcode_core.infrastructure.redis import (
    build_runtime_manage_control_payload,
    control_group,
    control_reply_stream,
    control_stream,
    redis_namespace,
)
from antcode_core.infrastructure.redis.factory import create_async_redis_client
from antcode_gateway.services.control_service import _settle_control_ack
from antcode_gateway.services.control_stream_ownership import (
    ControlAckOutcome,
    ControlPendingEntry,
    ack_owned_control_entry,
    control_consumer_name,
    recover_stale_control_entries,
)
from antcode_gateway.services.runtime_control_settlement_store import settlement_key

pytestmark = pytest.mark.asyncio

# settlement CAS 会校验 lease hash 的 PTTL，给足余量避免测试期间过期
_LEASE_TTL_MS = 60_000
_LEASE_ID = "lease-integration"


def _lease_key(worker_id: str) -> str:
    """与 runtime_control_settlement_store._lease_key 保持一致的 lease hash key。"""
    return f"{{{redis_namespace()}}}:lease:data:{worker_id}"


async def _grant_lease(redis, worker_id: str) -> str:
    """写入 settlement lease fence 所需的当前 lease 标记。"""
    key = _lease_key(worker_id)
    seconds, micros = await redis.time()
    expires_at_ms = int(seconds) * 1000 + int(micros) // 1000 + _LEASE_TTL_MS
    await redis.hset(key, mapping={"lease_id": _LEASE_ID, "expires_at_ms": expires_at_ms})
    await redis.pexpire(key, _LEASE_TTL_MS)
    return key


async def _pending_runtime_event(
    redis,
    *,
    worker_id: str,
    request_id: str,
    consumer: str,
) -> tuple[str, str]:
    stream = control_stream(worker_id)
    reply = control_reply_stream(request_id)
    await redis.xgroup_create(stream, control_group(), id="0", mkstream=True)
    message_id = await redis.xadd(
        stream,
        build_runtime_manage_control_payload(
            "list_envs",
            request_id,
            reply,
            payload={"scope": "shared"},
            expires_at_ms=4_102_444_800_000,
        ),
    )
    delivered = await redis.xreadgroup(
        control_group(),
        consumer,
        streams={stream: ">"},
        count=1,
    )
    assert delivered
    return stream, message_id


def _request(
    *,
    worker_id: str,
    event_id: str,
    request_id: str,
    data: str,
) -> control_pb2.AckControlRequest:
    return control_pb2.AckControlRequest(
        worker_id=worker_id,
        event_id=event_id,
        success=True,
        request_id=request_id,
        data_json=data,
        lease_id=_LEASE_ID,
    )


@pytest_asyncio.fixture
async def redis_client():
    url = os.environ.get("ANTCODE_INTEGRATION_REDIS_URL", "")
    if not url:
        pytest.fail("ANTCODE_INTEGRATION_REDIS_URL is required")
    redis = create_async_redis_client(url, decode_responses=True)
    try:
        yield redis
    finally:
        await redis.aclose()


async def test_runtime_settlement_is_idempotent_and_consumer_bound(redis_client):
    redis = redis_client
    worker_id = f"runtime-worker-{uuid.uuid4().hex}"
    request_id = uuid.uuid4().hex
    stream, message_id = await _pending_runtime_event(
        redis,
        worker_id=worker_id,
        request_id=request_id,
        consumer=f"{worker_id}:{_LEASE_ID}",
    )
    event_id = f"{stream}|{message_id}"
    request = _request(
        worker_id=worker_id,
        event_id=event_id,
        request_id=request_id,
        data='{"envs":["shared-py312"]}',
    )
    lease_key = await _grant_lease(redis, worker_id)
    keys = [stream, control_reply_stream(request_id), settlement_key(event_id), lease_key]
    try:
        assert await _settle_control_ack(
            redis,
            event_id=event_id,
            stream_key=stream,
            message_id=message_id,
            worker_id=worker_id,
            group=control_group(),
            request=request,
        )
        assert await _settle_control_ack(
            redis,
            event_id=event_id,
            stream_key=stream,
            message_id=message_id,
            worker_id=worker_id,
            group=control_group(),
            request=request,
        )
        entries = await redis.xrange(control_reply_stream(request_id))
        assert len(entries) == 1
        assert entries[0][1]["data"] == '{"envs":["shared-py312"]}'
        conflict = _request(
            worker_id=worker_id,
            event_id=event_id,
            request_id=request_id,
            data='{"envs":[]}',
        )
        with pytest.raises(ValueError, match="不同结果"):
            await _settle_control_ack(
                redis,
                event_id=event_id,
                stream_key=stream,
                message_id=message_id,
                worker_id=worker_id,
                group=control_group(),
                request=conflict,
            )
    finally:
        await redis.delete(*keys)


async def test_runtime_settlement_rejects_another_consumer(redis_client):
    redis = redis_client
    worker_id = f"runtime-worker-{uuid.uuid4().hex}"
    request_id = uuid.uuid4().hex
    stream, message_id = await _pending_runtime_event(
        redis,
        worker_id=worker_id,
        request_id=request_id,
        consumer="other-worker",
    )
    event_id = f"{stream}|{message_id}"
    request = _request(
        worker_id=worker_id,
        event_id=event_id,
        request_id=request_id,
        data="null",
    )
    keys = [stream, control_reply_stream(request_id), settlement_key(event_id)]
    try:
        with pytest.raises(ValueError, match="待确认队列"):
            await _settle_control_ack(
                redis,
                event_id=event_id,
                stream_key=stream,
                message_id=message_id,
                worker_id=worker_id,
                group=control_group(),
                request=request,
            )
        assert not await redis.exists(control_reply_stream(request_id))
    finally:
        await redis.delete(*keys)


async def test_control_pel_handoff_fences_stale_lease_generation(redis_client):
    redis = redis_client
    worker_id = f"control-worker-{uuid.uuid4().hex}"
    old_lease_id = f"lease-old-{uuid.uuid4().hex}"
    new_lease_id = f"lease-new-{uuid.uuid4().hex}"
    stream = control_stream(worker_id)
    group = control_group()
    lease_key = _lease_key(worker_id)
    old_consumer = control_consumer_name(worker_id, old_lease_id)
    new_consumer = control_consumer_name(worker_id, new_lease_id)
    try:
        await redis.hset(lease_key, mapping={"lease_id": old_lease_id})
        await redis.pexpire(lease_key, _LEASE_TTL_MS)
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        message_id = await redis.xadd(stream, {"control_type": "ping"})
        assert await redis.xreadgroup(group, old_consumer, streams={stream: ">"}, count=1)
        old_entry = ControlPendingEntry(
            worker_id,
            old_lease_id,
            stream,
            group,
            message_id,
            old_consumer,
        )

        await redis.hset(lease_key, mapping={"lease_id": new_lease_id})
        await redis.pexpire(lease_key, _LEASE_TTL_MS)
        current_message_id = await redis.xadd(stream, {"control_type": "ping"})
        assert await redis.xreadgroup(group, new_consumer, streams={stream: ">"}, count=1)
        with pytest.raises(LeaseConflictError, match="ACK 时 lease 已切代"):
            await ack_owned_control_entry(redis, old_entry)

        claimed = await recover_stale_control_entries(
            redis,
            channels=(SimpleNamespace(stream_key=stream, group=group),),
            worker_id=worker_id,
            lease_id=new_lease_id,
        )
        assert len(claimed) == 1
        assert claimed[0].message_id == message_id
        pending = await redis.xpending_range(stream, group, min=message_id, max=message_id, count=1)
        assert pending[0]["consumer"] == new_consumer
        current_pending = await redis.xpending_range(
            stream,
            group,
            min=current_message_id,
            max=current_message_id,
            count=1,
        )
        assert current_pending[0]["consumer"] == new_consumer
        new_entry = ControlPendingEntry(
            worker_id,
            new_lease_id,
            stream,
            group,
            message_id,
            new_consumer,
        )
        assert await ack_owned_control_entry(redis, new_entry) is ControlAckOutcome.ACKED
        current_entry = ControlPendingEntry(
            worker_id,
            new_lease_id,
            stream,
            group,
            current_message_id,
            new_consumer,
        )
        assert await ack_owned_control_entry(redis, current_entry) is ControlAckOutcome.ACKED
        assert await redis.xpending_range(stream, group, min=message_id, max=message_id, count=1) == []
    finally:
        await redis.delete(stream, lease_key)
