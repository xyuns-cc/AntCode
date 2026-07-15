from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .config import Stage
from .metrics import LoadReport, OperationSample

T = TypeVar("T")
Operation = Callable[[int], Awaitable["OperationResult[T]"]]


@dataclass(frozen=True)
class OperationResult(Generic[T]):
    status_code: int
    value: T | None = None
    error: str | None = None


async def run_load(name: str, stage: Stage, operation: Operation[T]) -> LoadReport[T]:
    semaphore = asyncio.Semaphore(stage.vus)
    samples: list[tuple[int, OperationSample[T]]] = []
    started = time.perf_counter()
    tasks = await _schedule(stage, semaphore, operation, samples=samples, started=started)
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started
    ordered = tuple(sample for _, sample in sorted(samples, key=lambda item: item[0]))
    return LoadReport(name, stage, elapsed, ordered)


async def _schedule(
    stage: Stage,
    semaphore: asyncio.Semaphore,
    operation: Operation[T],
    *,
    samples: list[tuple[int, OperationSample[T]]],
    started: float,
) -> list[asyncio.Task[None]]:
    interval = 1.0 / stage.qps
    tasks: list[asyncio.Task[None]] = []
    for index in range(stage.request_count):
        release_at = started + index * interval
        await asyncio.sleep(max(0.0, release_at - time.perf_counter()))
        submitted_at = time.perf_counter()
        task = asyncio.create_task(
            _execute(
                index,
                semaphore,
                operation,
                samples=samples,
                submitted_at=submitted_at,
            )
        )
        tasks.append(task)
    return tasks


async def _execute(
    index: int,
    semaphore: asyncio.Semaphore,
    operation: Operation[T],
    *,
    samples: list[tuple[int, OperationSample[T]]],
    submitted_at: float,
) -> None:
    async with semaphore:
        try:
            result = await operation(index)
            elapsed_ms = (time.perf_counter() - submitted_at) * 1_000
            samples.append((index, OperationSample(elapsed_ms, result.status_code, result.value, result.error)))
        except Exception as exc:  # noqa: BLE001 - failures are measured, then asserted
            elapsed_ms = (time.perf_counter() - submitted_at) * 1_000
            samples.append((index, OperationSample(elapsed_ms, None, error=type(exc).__name__)))
