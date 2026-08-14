"""Cancellation-safe singleflight for concurrent source-bundle builds."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")
SOURCE_BUNDLE_MEDIA_TYPE = "application/vnd.antcode.source-bundle+tar-gzip"
MAX_BUNDLE_ARCHIVE_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class SourceBundle:
    uri: str
    sha256: str
    size_bytes: int
    entry_point: str
    resolved_revision: str
    artifact_id: int


def bundle_request_key(source_config: dict[str, object], entry_point: str | None) -> str:
    canonical = json.dumps(
        {"source": source_config, "entry_point": entry_point or ""},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AsyncSingleFlight(Generic[KeyT, ValueT]):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[KeyT, asyncio.Task[ValueT]] = {}

    async def run(self, key: KeyT, operation: Callable[[], Awaitable[ValueT]]) -> ValueT:
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(self._execute(key, operation))
                task.add_done_callback(self._consume_exception)
                self._tasks[key] = task
        return await asyncio.shield(task)

    async def _execute(self, key: KeyT, operation: Callable[[], Awaitable[ValueT]]) -> ValueT:
        try:
            return await operation()
        finally:
            current = asyncio.current_task()
            async with self._lock:
                if self._tasks.get(key) is current:
                    self._tasks.pop(key)

    @staticmethod
    def _consume_exception(task: asyncio.Task[ValueT]) -> None:
        if not task.cancelled():
            task.exception()


__all__ = [
    "AsyncSingleFlight",
    "MAX_BUNDLE_ARCHIVE_BYTES",
    "SOURCE_BUNDLE_MEDIA_TYPE",
    "SourceBundle",
    "bundle_request_key",
]
