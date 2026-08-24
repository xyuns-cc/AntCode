"""释放节奏的本地判据：一场压测有没有真的制造出并发，只能测出来。

``trigger-dedup`` 曾经全过而全程零并发——匀速释放下 ``1/QPS`` 远大于一次触发的
耗时，在飞请求恒为 1；而触发去重锁按 TTL 生效，串行释放的后续请求照样返回
409，状态码分布看上去和一次真竞态一模一样。这里把"释放节奏 → 峰值并发度"
这条判据钉住：匀速释放必须测出 1，同一份配置改成同时释放才够得着竞态门槛。
"""

from __future__ import annotations

import asyncio

import pytest

from tests.loadtest.test_task_throughput import MIN_CONCURRENT_TRIGGERS
from tests.loadtest.tool.config import Stage
from tests.loadtest.tool.metrics import peak_overlap
from tests.loadtest.tool.runner import OperationResult, run_burst, run_load

# 单次操作耗时远小于匀速释放的间隔（1/20 秒），这正是触发接口的真实比例。
OPERATION_SECONDS = 0.02
SERIAL_PEAK = 1
OVERLAPPING_PAIR = 2
NESTED_TRIPLE = 3
STAGE = Stage(5, 20, 0.25)


async def _operation(_index: int) -> OperationResult[str]:
    await asyncio.sleep(OPERATION_SECONDS)
    return OperationResult(200, "ok")


def test_peak_overlap_counts_simultaneous_windows_only() -> None:
    assert peak_overlap([]) == 0
    assert peak_overlap([(0.0, 1.0)]) == SERIAL_PEAK
    # 首尾相接不是重叠：前一个请求已经返回，后一个才发出。
    assert peak_overlap([(0.0, 1.0), (1.0, 2.0)]) == SERIAL_PEAK
    assert peak_overlap([(0.0, 2.0), (1.0, 3.0), (2.5, 4.0)]) == OVERLAPPING_PAIR
    assert peak_overlap([(0.0, 9.0), (1.0, 8.0), (2.0, 7.0)]) == NESTED_TRIPLE


@pytest.mark.asyncio
async def test_paced_release_never_overlaps_a_fast_operation() -> None:
    report = await run_load("paced", STAGE, _operation)
    assert report.summary.requests == STAGE.request_count
    assert report.peak_concurrency == SERIAL_PEAK
    assert report.peak_concurrency < MIN_CONCURRENT_TRIGGERS


@pytest.mark.asyncio
async def test_burst_release_overlaps_up_to_the_configured_vus() -> None:
    report = await run_burst("burst", STAGE, _operation)
    assert report.summary.requests == STAGE.request_count
    assert report.peak_concurrency == min(STAGE.vus, STAGE.request_count)
    assert report.peak_concurrency >= MIN_CONCURRENT_TRIGGERS
