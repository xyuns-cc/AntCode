"""Helpers for asserting protobuf payloads stored in Redis Streams."""

from __future__ import annotations

from typing import TypeVar

import redis.asyncio as aioredis
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, ProtoMessage

P = TypeVar("P", bound=ProtoMessage)


async def read_proto_stream(
    redis_url: str,
    stream_key: str,
    message_type: type[P],
) -> list[P]:
    """Read and decode the single-field protobuf entries in a stream."""
    client = aioredis.from_url(redis_url, decode_responses=False)
    try:
        entries = await client.xrange(stream_key)
    finally:
        await client.aclose()

    codec = ProtoCodec(message_type)
    return [codec.decode(fields) for _message_id, fields in entries]
