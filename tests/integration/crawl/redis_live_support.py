"""Isolated real-Redis support for Crawl integration contracts."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
REDIS_REQUIRED_REASON = "ANTCODE_INTEGRATION_REDIS_URL is required"


@asynccontextmanager
async def scoped_redis() -> AsyncIterator[tuple[aioredis.Redis, str]]:
    if REDIS_URL is None:
        raise RuntimeError(REDIS_REQUIRED_REASON)
    namespace = f"crawl-live-{uuid.uuid4().hex}"
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    connected = False
    try:
        await redis.ping()
        connected = True
        yield redis, namespace
    finally:
        if connected:
            keys = [key async for key in redis.scan_iter(match=f"*{namespace}*")]
            if keys:
                await redis.delete(*keys)
        await redis.aclose()
