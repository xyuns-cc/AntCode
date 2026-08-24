from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .config import Stage
from .metrics import InFlightWindow, LoadReport, OperationSample, peak_overlap

T = TypeVar("T")
Operation = Callable[[int], Awaitable["OperationResult[T]"]]
BURST_INTERVAL_SECONDS = 0.0
MILLISECONDS_PER_SECOND = 1_000


@dataclass(frozen=True)
class OperationResult(Generic[T]):
    status_code: int
    value: T | None = None
    error: str | None = None


async def run_load(name: str, stage: Stage, operation: Operation[T]) -> LoadReport[T]:
    """按 ``1/QPS`` 匀速释放请求，用于测吞吐与延迟。"""
    return await _run(name, stage, operation, interval=1.0 / stage.qps)


async def run_burst(name: str, stage: Stage, operation: Operation[T]) -> LoadReport[T]:
    """不留间隔地释放全部请求，在飞并发由 VUS 决定；用于测竞态。

    匀速释放下在飞请求数约等于 ``QPS x 单次耗时``。触发类接口只要十几毫秒，
    想靠 QPS 把这个乘积抬到 2 以上就得每秒几百次请求，直接撞死全局限流。
    要打真并发只能同时释放，否则场景会全过而全程零并发。
    """
    return await _run(name, stage, operation, interval=BURST_INTERVAL_SECONDS)


async def _run(name: str, stage: Stage, operation: Operation[T], *, interval: float) -> LoadReport[T]:
    semaphore = asyncio.Semaphore(stage.vus)
    samples: list[tuple[int, OperationSample[T]]] = []
    started = time.perf_counter()
    tasks = await _schedule(stage, semaphore, operation, samples=samples, started=started, interval=interval)
    windows = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started
    ordered = tuple(sample for _, sample in sorted(samples, key=lambda item: item[0]))
    return LoadReport(name, stage, elapsed, ordered, peak_overlap(windows))


async def _schedule(
    stage: Stage,
    semaphore: asyncio.Semaphore,
    operation: Operation[T],
    *,
    samples: list[tuple[int, OperationSample[T]]],
    started: float,
    interval: float,
) -> list[asyncio.Task[InFlightWindow]]:
    tasks: list[asyncio.Task[InFlightWindow]] = []
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
) -> InFlightWindow:
    """返回该请求真正在飞的时间窗口，供峰值并发度统计。"""
    async with semaphore:
        # 窗口起点取在拿到 VUS 名额之后：排队期间请求还没上路，算进去会把
        # 串行释放误报成并发。
        started_at = time.perf_counter()
        try:
            result = await operation(index)
            sample = OperationSample(_latency_ms(submitted_at), result.status_code, result.value, result.error)
        except Exception as exc:  # noqa: BLE001 - failures are measured, then asserted
            sample = OperationSample[T](_latency_ms(submitted_at), None, error=type(exc).__name__)
        finished_at = time.perf_counter()
    samples.append((index, sample))
    return started_at, finished_at


def _latency_ms(submitted_at: float) -> float:
    return (time.perf_counter() - submitted_at) * MILLISECONDS_PER_SECOND
