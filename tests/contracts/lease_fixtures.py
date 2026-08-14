"""Shared real-Redis fixtures for LeaseStore contract modules."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
import redis.asyncio as aioredis
from antcode_core.application.services.lease_service import LeasePolicy, LeaseStore

from tests.contracts.conftest import REDIS_TEST_HOST, REDIS_TEST_PORT, REDIS_TEST_URL, _tcp_reachable


async def redis_client_fixture() -> AsyncIterator[Any]:
    if not _tcp_reachable(REDIS_TEST_HOST, REDIS_TEST_PORT):
        pytest.fail(f"Redis on {REDIS_TEST_HOST}:{REDIS_TEST_PORT} unreachable")
    client = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
        yield client
    finally:
        await client.aclose()


async def lease_store_fixture(redis_client: Any) -> AsyncIterator[LeaseStore]:
    namespace = f"antcode-test-{secrets.token_hex(4)}"
    store = LeaseStore(redis_client, namespace=namespace, policy=LeasePolicy(ttl_ms=2_000, renew_after_ms=500))
    try:
        yield store
    finally:
        await _delete_namespace(redis_client, namespace)


async def _delete_namespace(redis_client: Any, namespace: str) -> None:
    for pattern in (f"{{{namespace}}}:*", f"{namespace}:*"):
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(cursor=cursor, match=pattern, count=500)
            if keys:
                await redis_client.delete(*keys)
            if cursor == 0:
                break


__all__ = ["lease_store_fixture", "redis_client_fixture"]
