"""Shared helpers for fenced Direct transport integration tests."""

from typing import Any


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
