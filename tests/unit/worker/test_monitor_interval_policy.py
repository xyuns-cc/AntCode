"""监控间隔必须由**限额**定档，而不是由任务自选的 ``timeout_seconds``。

旧实现按任务自己的 ``timeout_seconds`` 分档（≤60s→0.5s ... >1800s→5.0s）。
``timeout_seconds`` 是普通任务字段（``gt=0``、默认 3600、无上限），于是超限窗口
（≈ 采样间隔 × 弄脏页速率）由任务作者自己选：真机实测同一个快速分配用例、
同为 512MB 限额，timeout=300 被杀在 1924MB，timeout=3600 被杀在 3020MB。

这里的用例全部是证伪项：把 ``resource_monitor_interval`` 换回按 timeout 分档，
或把 ``process.py`` 换回 ``_get_monitor_interval(exec_plan)``，都会变红。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

import pytest
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor import process as process_mod
from antcode_worker.executor.monitor_interval import resource_monitor_interval

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")

# 独立于实现常数的实测弄脏页速率：上一轮终验在测试机上测到 1536MB/0.838s，
# 本轮在 Worker 容器内用 MAP_SHARED 4MiB 分块写测到 1733 MB/s。取两者较大者，
# 用来把"间隔"翻译成"窗口"，从而断言超限倍数而不是断言公式本身。
_MEASURED_DIRTY_RATE_MB_PER_SECOND = 1830.0

# 目标：被杀点不超过限额的 1.5 倍（留 0.1 给采样与 SIGKILL 投递的抖动）。
_MAX_ACCEPTABLE_BREACH_MULTIPLE = 1.6

# 旧实现的四个档位，用来证明它们已经不再影响结果
_LEGACY_TIMEOUT_BUCKETS = (60, 300, 1800, 3600)
_LEGACY_WORST_INTERVAL_SECONDS = 5.0

_TYPICAL_LIMITS_MB = (256, 512, 1024, 2150, 2808, 4096, 8192)
# 0.1s 采样下限之上，倍数保证才成立（下界 ≈ 0.1 × 2048 / 0.5 = 410MB）
_LIMITS_ABOVE_FLOOR_MB = (512, 1024, 2150, 2808, 4096, 8192)
_SMALL_LIMIT_MB = 256
_LARGE_LIMIT_MB = 8192
_MIN_EXPECTED_INTERVAL_SECONDS = 0.1
_MAX_EXPECTED_INTERVAL_SECONDS = 1.0
# 采样下限档的窗口绝对值上限：0.1s × 1830MB/s ≈ 183MB，留一点取整余量
_FLOOR_WINDOW_MB = 190.0

_MONITOR_TIMEOUT_SECONDS = 30
_PROBE_LIMIT_MB = 777
_PROBE_SLEEP_SECONDS = 600
_POLL_SECONDS = 0.01


def test_interval_tightens_as_the_limit_shrinks() -> None:
    """限额越小越要密采样：窗口的绝对值必须随限额一起缩小。"""
    intervals = [resource_monitor_interval(limit) for limit in _TYPICAL_LIMITS_MB]

    assert intervals == sorted(intervals), f"间隔必须随限额单调不减: {list(zip(_TYPICAL_LIMITS_MB, intervals))}"
    assert resource_monitor_interval(_SMALL_LIMIT_MB) < resource_monitor_interval(_LARGE_LIMIT_MB)


@pytest.mark.parametrize("limit_mb", _LIMITS_ABOVE_FLOOR_MB)
def test_breach_multiple_stays_bounded_above_the_polling_floor(limit_mb: int) -> None:
    """按实测弄脏速率外推，被杀点不得超过限额的 1.6 倍。

    旧实现在 5.0s 档下窗口 ≈ 9.2GB，512MB 限额对应 19 倍——这条会直接把它拦下。
    """
    window_mb = resource_monitor_interval(limit_mb) * _MEASURED_DIRTY_RATE_MB_PER_SECOND
    breach_multiple = (limit_mb + window_mb) / limit_mb

    assert breach_multiple <= _MAX_ACCEPTABLE_BREACH_MULTIPLE, (
        f"限额 {limit_mb}MB 的超限倍数 {breach_multiple:.2f}× 超过上限 {_MAX_ACCEPTABLE_BREACH_MULTIPLE}×"
    )


def test_smallest_limit_is_bounded_by_the_polling_floor_instead() -> None:
    """限额小到 0.1s 采样下限先生效时，保证从"倍数"退化为"窗口绝对值"。

    这是必须写清的边界，不是漏网：256MB 限额下窗口约 183MB（≈1.7×），
    继续压只能靠加密轮询，而轮询开销是实打实的成本。
    """
    window_mb = resource_monitor_interval(_SMALL_LIMIT_MB) * _MEASURED_DIRTY_RATE_MB_PER_SECOND

    assert window_mb <= _FLOOR_WINDOW_MB, f"下限档窗口 {window_mb:.0f}MB 超过 {_FLOOR_WINDOW_MB}MB"


@pytest.mark.parametrize("limit_mb", _TYPICAL_LIMITS_MB)
def test_interval_stays_inside_the_polling_cost_envelope(limit_mb: int) -> None:
    """间隔必须钳在 [0.1s, 1.0s]：下界护住轮询开销，上界护住 CPU 上限的检出延迟。

    单次 ``sample_process_tree`` 容器内实测 0.5ms，0.1s 间隔下每个被监控任务
    约占单核 0.5%。
    """
    interval = resource_monitor_interval(limit_mb)

    assert _MIN_EXPECTED_INTERVAL_SECONDS <= interval <= _MAX_EXPECTED_INTERVAL_SECONDS
    assert interval < _LEGACY_WORST_INTERVAL_SECONDS


def test_no_memory_limit_uses_the_coarsest_interval() -> None:
    """只配了 CPU 上限时按最粗档采样——这是显式定义的行为，不是兜底。"""
    assert resource_monitor_interval(0) == _MAX_EXPECTED_INTERVAL_SECONDS


@pytest.mark.parametrize("timeout_seconds", _LEGACY_TIMEOUT_BUCKETS)
@pytest.mark.asyncio
async def test_monitor_asks_for_the_interval_using_the_memory_limit(
    timeout_seconds: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_monitor_resources`` 必须拿**内存限额**去问间隔，而不是拿 timeout。

    这条锁住 process.py 侧的接线，并且逐个覆盖旧实现的四个 timeout 档位：
    四档拿到的输入都必须是同一个限额，也就是窗口大小不再由任务作者选。
    换回 ``_get_monitor_interval(exec_plan)`` 会让记录到的参数变成 timeout，变红。
    """
    recorded: list[int] = []

    def _spy(memory_limit_mb: int) -> float:
        recorded.append(memory_limit_mb)
        return _MIN_EXPECTED_INTERVAL_SECONDS

    monkeypatch.setattr(process_mod, "resource_monitor_interval", _spy)

    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", f"import time; time.sleep({_PROBE_SLEEP_SECONDS})"
    )
    info = process_mod.ProcessInfo(
        process=process,
        run_id="run-interval-probe",
        started_at=datetime.now(),
        # timeout 走遍旧实现的四个档位；限额恒定，间隔就必须恒定
        exec_plan=ExecPlan(
            command=sys.executable,
            run_id="run-interval-probe",
            memory_limit_mb=_PROBE_LIMIT_MB,
            timeout_seconds=timeout_seconds,
        ),
    )
    monitor = asyncio.create_task(process_mod.ProcessExecutor()._monitor_resources(info))

    try:
        await asyncio.wait_for(_wait_until_recorded(recorded), timeout=_MONITOR_TIMEOUT_SECONDS)
    finally:
        monitor.cancel()
        process.kill()
        await process.wait()

    assert recorded == [_PROBE_LIMIT_MB], f"采样间隔的输入必须是内存限额，实际拿到 {recorded}"


async def _wait_until_recorded(recorded: list[int]) -> None:
    while not recorded:
        await asyncio.sleep(_POLL_SECONDS)
