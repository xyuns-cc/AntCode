"""Cancellation-aware registry for Worker preparation operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class PreparationCancelledError(Exception):
    """A run was cancelled while its preparation operation was active."""


class PreparationTaskRegistry:
    """Expose one active preparation task per run to the control loop."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Future[Any]] = {}

    async def run(self, run_id: str, operation: Callable[[], Awaitable[T]]) -> T:
        if run_id in self._tasks:
            raise RuntimeError(f"运行已有准备任务: run_id={run_id}")
        task: asyncio.Future[T] = asyncio.ensure_future(operation())
        self._tasks[run_id] = task
        try:
            return await task
        except asyncio.CancelledError as error:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            raise PreparationCancelledError(run_id) from error
        finally:
            if self._tasks.get(run_id) is task:
                self._tasks.pop(run_id, None)

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        if isinstance(outcome, asyncio.CancelledError):
            return True
        if isinstance(outcome, BaseException):
            raise outcome
        return True


__all__ = ["PreparationCancelledError", "PreparationTaskRegistry"]
