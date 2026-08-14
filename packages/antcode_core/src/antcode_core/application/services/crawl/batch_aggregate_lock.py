"""Cross-process serialization for Crawl batch lifecycle events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from tortoise import Tortoise

_LOCK_SQL = "SELECT pg_advisory_lock(hashtextextended($1, 0))"
_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended($1, 0))"
_LOCK_DOMAIN = "antcode:crawl-batch:"


async def _release_lock(connection, lock_name: str, batch_id: str) -> None:
    unlock_task = asyncio.create_task(connection.fetchval(_UNLOCK_SQL, lock_name))
    cancellation: asyncio.CancelledError | None = None
    try:
        await asyncio.shield(unlock_task)
    except asyncio.CancelledError as exc:
        cancellation = exc
        await unlock_task
    if unlock_task.result() is not True:
        raise RuntimeError(f"crawl batch aggregate lock lost: batch_id={batch_id}")
    if cancellation is not None:
        raise cancellation


@asynccontextmanager
async def crawl_batch_aggregate_lock(batch_id: str) -> AsyncIterator[None]:
    """Hold one PostgreSQL session lock for the full lifecycle side effect."""
    client = Tortoise.get_connection("default")
    lock_name = f"{_LOCK_DOMAIN}{batch_id}"
    async with client.acquire_connection() as connection:
        await connection.execute(_LOCK_SQL, lock_name)
        try:
            yield
        finally:
            await _release_lock(connection, lock_name, batch_id)


__all__ = ["crawl_batch_aggregate_lock"]
