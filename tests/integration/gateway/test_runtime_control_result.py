from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from antcode_contracts import control_pb2
from antcode_core.infrastructure.redis import (
    build_runtime_manage_control_payload,
    control_group,
    control_reply_stream,
    control_stream,
    redis_namespace,
)
from antcode_core.infrastructure.redis.factory import create_async_redis_client
from antcode_gateway.services.control_service import _settle_control_ack
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
    await redis.hset(key, mapping={"lease_id": _LEASE_ID})
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
        consumer=worker_id,
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
