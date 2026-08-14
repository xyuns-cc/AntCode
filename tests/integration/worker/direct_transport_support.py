"""Shared helpers for fenced Direct transport integration tests."""

from typing import Any

# B12: publish_ready_batch 必传 Master 代际，且 Redis 里的代际键必须匹配。
# 集成环境没有真 Master 写这把键，由 helper 自己预置同一个值。
DISPATCH_EPOCH = 19


def source_bundle_fields(digest_character: str) -> dict[str, str]:
    digest = digest_character * 64
    return {
        "source_bundle_uri": f"pgartifact://{digest}",
        "source_bundle_sha256": digest,
        "source_bundle_size": "128",
        "transfer_method": "source_bundle",
    }


async def activate_direct_transport(transport: Any) -> str:
    assert await transport.start()
    lease_id, _, _, revoked = await transport.lease_renew("")
    assert lease_id and not revoked
    return str(lease_id)


async def publish_ready_task(redis: Any, transport: Any, payload: dict[str, Any]) -> str:
    from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
    from antcode_core.application.services.lease_fenced_ready_publish import publish_ready_batch
    from antcode_core.infrastructure.redis.control_plane import scheduler_dispatch_fencing_key

    lease_store = transport._lease_store
    worker_id = transport._worker_id
    lease = await lease_store.get(worker_id, include_expired=False)
    assert lease is not None and lease.lease_id == transport._lease_id
    capabilities_json = await redis.hget(lease_store.lease_key(worker_id), "capabilities_json")
    assert capabilities_json and lease.sequence > 0
    snapshot = LeaseCapabilitySnapshot(
        lease_id=lease.lease_id,
        capabilities_json=str(capabilities_json),
        lease_gen=lease.sequence,
    )
    namespace = transport._keys.namespace
    await redis.set(scheduler_dispatch_fencing_key(namespace=namespace), str(DISPATCH_EPOCH))
    message_ids = await publish_ready_batch(
        redis,
        worker_id=worker_id,
        snapshot=snapshot,
        messages=[payload],
        scheduler_fencing_token=DISPATCH_EPOCH,
        namespace=namespace,
        worker_secret=transport._task_payload_secret,
    )
    return message_ids[0]


async def stop_direct_transport(transport: Any) -> None:
    await transport.deregister("integration-test-cleanup")
    await transport.stop()


def decode_task_statuses(entries: list[Any]) -> list[Any]:
    from antcode_contracts import data_pb2
    from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD

    return [data_pb2.TaskStatus.FromString(fields[PROTO_FIELD]) for _, fields in entries]


async def read_task_statuses(redis_url: str, result_key: str, *, count: int) -> list[Any]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=False)
    try:
        entries = await client.xrevrange(result_key, "+", "-", count=count)
        return decode_task_statuses(entries)
    finally:
        await client.aclose()
