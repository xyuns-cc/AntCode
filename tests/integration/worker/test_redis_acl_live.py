import os
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest
import redis.asyncio as aioredis
from antcode_core.common.security.redis_acl import _build_setuser_args
from antcode_worker.transport.redis.task_settlement import (
    _task_marker_key,
    ack_owned_task,
    requeue_marker_key,
    requeue_owned_task,
)
from redis.exceptions import AuthenticationError, NoPermissionError

from tests.integration.worker.redis_acl_live_support import assert_worker_lease_access

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ANTCODE_INTEGRATION_REDIS_URL is required")


@dataclass(frozen=True)
class _AclArtifacts:
    settlement_keys: tuple[str, str]
    result_ingest_id: str
    task_markers: tuple[str, str]
    spider_keys: tuple[str, ...]


def _worker_client(username: str, password: str):
    parsed = urlsplit(REDIS_URL or "")
    return aioredis.Redis(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        db=int(parsed.path.removeprefix("/") or 0),
        username=username,
        password=password,
        decode_responses=True,
        ssl=parsed.scheme == "rediss",
    )


async def _set_worker_acl(admin, username: str, password: str, *, worker_id: str) -> None:
    args = _build_setuser_args(username, password, worker_id, namespace="antcode")
    await admin.execute_command("ACL", *args)


async def _assert_result_ingest_is_xadd_only(client) -> str:
    result_id = await client.xadd("antcode:task:result", {"payload": "result"})
    for operation in (
        lambda: client.xrange("antcode:task:result"),
        lambda: client.xdel("antcode:task:result", result_id),
        lambda: client.xtrim("antcode:task:result", maxlen=0),
    ):
        with pytest.raises(NoPermissionError):
            await operation()
    with pytest.raises(NoPermissionError):
        await client.xadd("antcode:log:ingest", {"payload": "log"})
    return result_id


async def _assert_task_settlement_acl(
    admin,
    client,
    *,
    worker_id: str,
    suffix: str,
) -> tuple[str, str]:
    stream = f"{{antcode}}:task:ready:{worker_id}"
    group = f"antcode-workers-acl-{suffix}"
    consumer = f"worker:{worker_id}"
    source_id = await admin.xadd(stream, {"task_id": "task-1"})
    await client.xgroup_create(stream, group, id="0-0")
    messages = await client.xreadgroup(group, consumer, {stream: ">"}, count=1)
    assert messages[0][1][0][0] == source_id
    requeued_id = await requeue_owned_task(
        client,
        stream_key=stream,
        group=group,
        message_id=source_id,
        consumer_name=consumer,
        payload={"task_id": "task-1", "requeue_count": "1"},
    )
    requeued = await client.xreadgroup(group, consumer, {stream: ">"}, count=1)
    assert requeued[0][1][0][0] == requeued_id
    assert (
        await ack_owned_task(
            client,
            stream_key=stream,
            group=group,
            message_id=requeued_id,
            consumer_name=consumer,
        )
        == 1
    )
    return requeue_marker_key(stream, source_id), _task_marker_key(stream, requeued_id, "ack")


async def _assert_spider_and_run_write_acl(client, suffix: str) -> tuple[str, ...]:
    run_id = f"run-{suffix}"
    data_key = f"{{antcode}}:spider:{run_id}:data"
    meta_key = f"{{antcode}}:spider:{run_id}:meta"
    tombstone_key = f"{{antcode}}:spider:{run_id}:tombstone"
    index_key = f"{{antcode}}:spider:index:project-{suffix}"
    dedup_key = f"{{antcode}}:spider:{run_id}:item-ids"
    owner_key = f"{{antcode}}:run:owner:{run_id}"
    for operation in (
        lambda: client.xadd(data_key, {"item_id": "item-1"}),
        lambda: client.hset(meta_key, mapping={"status": "running"}),
        lambda: client.set(tombstone_key, "deleted"),
        lambda: client.zadd(index_key, {run_id: 1.0}),
        lambda: client.hset(dedup_key, mapping={"item:item-1": "digest"}),
        lambda: client.set(owner_key, "owner", nx=True, ex=60),
        lambda: client.get(owner_key),
        lambda: client.delete(owner_key),
        lambda: client.pexpire(owner_key, 60_000),
    ):
        with pytest.raises(NoPermissionError):
            await operation()
    with pytest.raises(NoPermissionError):
        await client.eval("return redis.call('XADD', KEYS[1], '*', 'item_id', 'item-1')", 1, data_key)
    return data_key, meta_key, tombstone_key, index_key, dedup_key, owner_key


async def _assert_global_control_is_trusted_only(admin, client, suffix: str) -> None:
    stream = "antcode:control:global"
    group = f"antcode-control:acl-live-{suffix}"
    message_id = await admin.xadd(stream, {"control_type": "ping"})
    try:
        assert await admin.xgroup_create(stream, group, id="0-0")
        messages = await admin.xreadgroup(group, "trusted", {stream: ">"}, count=1)
        assert messages[0][1][0][0] == message_id
        denied_operations = (
            lambda: client.xgroup_create(stream, f"attacker-{suffix}", id="0-0"),
            lambda: client.xreadgroup(group, suffix, {stream: ">"}, count=1),
            lambda: client.xpending(stream, group),
            lambda: client.xclaim(stream, group, suffix, 0, [message_id]),
            lambda: client.xack(stream, group, message_id),
            lambda: client.eval("return redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])", 1, stream, group, message_id),
            lambda: client.xadd(stream, {"control_type": "cancel"}),
            lambda: client.xdel(stream, message_id),
            lambda: client.xtrim(stream, maxlen=0),
            lambda: client.xgroup_destroy(stream, group),
        )
        for operation in denied_operations:
            with pytest.raises(NoPermissionError):
                await operation()
    finally:
        await admin.xgroup_destroy(stream, group)
        await admin.xdel(stream, message_id)


async def _exercise_worker_acl(
    admin,
    client,
    *,
    worker_id: str,
    other_heartbeat_key: str,
    other_lease_key: str,
) -> _AclArtifacts:
    heartbeat_key = f"{{antcode}}:heartbeat:{worker_id}"
    proof_key = f"antcode:direct:register:{worker_id}"
    assert await client.hset(heartbeat_key, mapping={"status": "online"}) == 1
    assert await client.expire(heartbeat_key, 60)
    assert await client.set(proof_key, "proof", ex=60)
    settlement_keys = await assert_worker_lease_access(
        admin,
        client,
        worker_id=worker_id,
        other_lease_key=other_lease_key,
    )
    await _assert_global_control_is_trusted_only(admin, client, uuid.uuid4().hex)
    result_id = await _assert_result_ingest_is_xadd_only(client)
    task_markers = await _assert_task_settlement_acl(
        admin,
        client,
        worker_id=worker_id,
        suffix=uuid.uuid4().hex,
    )
    spider_keys = await _assert_spider_and_run_write_acl(client, uuid.uuid4().hex)
    with pytest.raises(NoPermissionError):
        await client.hset(other_heartbeat_key, mapping={"status": "forbidden"})
    with pytest.raises(NoPermissionError):
        await client.scan()
    return _AclArtifacts(settlement_keys, result_id, task_markers, spider_keys)


async def _cleanup_acl_test(
    admin,
    *,
    worker_id: str,
    username: str,
    other_heartbeat_key: str,
    other_lease_key: str,
    artifacts: _AclArtifacts | None,
) -> None:
    cleanup_keys = [
        f"{{antcode}}:heartbeat:{worker_id}",
        other_heartbeat_key,
        f"{{antcode}}:lease:data:{worker_id}",
        f"{{antcode}}:lease:revoked:{worker_id}",
        other_lease_key,
        f"antcode:direct:register:{worker_id}",
        f"{{antcode}}:task:ready:{worker_id}",
    ]
    if artifacts is not None:
        cleanup_keys.extend(artifacts.settlement_keys)
        cleanup_keys.extend(artifacts.spider_keys)
        cleanup_keys.extend(artifacts.task_markers)
        await admin.xdel("antcode:task:result", artifacts.result_ingest_id)
    await admin.delete(*cleanup_keys)
    await admin.zrem("{antcode}:lease:expiring", worker_id)
    await admin.srem("{antcode}:lease:active", worker_id)
    await admin.execute_command("ACL", "DELUSER", username)
    await admin.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_acl_rotation_and_key_isolation_on_real_redis() -> None:
    worker_id = f"acl-live-{uuid.uuid4().hex[:20]}"
    username = f"worker_{worker_id}"
    other_key = f"{{antcode}}:heartbeat:other-{uuid.uuid4().hex}"
    other_lease_key = f"{{antcode}}:lease:data:other-{uuid.uuid4().hex}"
    admin = aioredis.from_url(REDIS_URL, decode_responses=True)
    artifacts: _AclArtifacts | None = None
    first_password = f"first-{uuid.uuid4().hex}"
    second_password = f"second-{uuid.uuid4().hex}"
    try:
        await _set_worker_acl(admin, username, first_password, worker_id=worker_id)
        first_client = _worker_client(username, first_password)
        try:
            artifacts = await _exercise_worker_acl(
                admin,
                first_client,
                worker_id=worker_id,
                other_heartbeat_key=other_key,
                other_lease_key=other_lease_key,
            )
        finally:
            await first_client.aclose()

        await _set_worker_acl(admin, username, second_password, worker_id=worker_id)
        stale_client = _worker_client(username, first_password)
        current_client = _worker_client(username, second_password)
        try:
            with pytest.raises(AuthenticationError):
                await stale_client.ping()
            assert await current_client.ping() is True
        finally:
            await stale_client.aclose()
            await current_client.aclose()
    finally:
        await _cleanup_acl_test(
            admin,
            worker_id=worker_id,
            username=username,
            other_heartbeat_key=other_key,
            other_lease_key=other_lease_key,
            artifacts=artifacts,
        )
