"""Dynamically resizable concurrency control for task executors."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionAdmission:
    """Handshake that makes an execution cancellable before user code starts."""

    registered: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    async def executor_ready(self) -> None:
        """Publish the cancellation handle, then wait for Engine admission."""
        self.registered.set()
        await self.released.wait()

    def release(self) -> None:
        self.released.set()

    async def wait_until_registered_or_done(self, execution: asyncio.Task) -> bool:
        """Return whether registration won the race against early execution failure."""
        waiter = asyncio.create_task(self.registered.wait())
        try:
            done, _ = await asyncio.wait(
                {execution, waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            return waiter in done and self.registered.is_set()
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


class ResizableConcurrencyGate:
    """Limit entrants while allowing in-flight work to finish after shrink."""

    def __init__(self, limit: int) -> None:
        self._require_limit(limit)
        self._limit = limit
        self._active = 0
        self._condition = asyncio.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._acquire()
        try:
            yield
        finally:
            await self._release()

    async def resize(self, limit: int) -> None:
        self._require_limit(limit)
        async with self._condition:
            increased = limit > self._limit
            self._limit = limit
            if increased:
                self._condition.notify_all()

    async def _acquire(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self._limit)
            self._active += 1

    async def _release(self) -> None:
        async with self._condition:
            if self._active <= 0:
                raise RuntimeError("并发槽位释放次数超过获取次数")
            self._active -= 1
            self._condition.notify_all()

    @staticmethod
    def _require_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("并发限制必须是正整数")


__all__ = ["ExecutionAdmission", "ResizableConcurrencyGate"]
