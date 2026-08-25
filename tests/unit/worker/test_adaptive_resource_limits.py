"""自适应限额必须从容器预算推导，且并发 × 限额不得超卖容器额度。

旧实现用 ``psutil.virtual_memory().total``（容器里读到的是宿主），并且把单任务
限额按**自适应自己算出的并发**去除，而生效并发可能是 env 钉死的另一个数——
真机实测就是这个状态：并发 env=4，限额却是按自适应的 8 除出来的 2808MB，
4 × 2808 = 11.2GB 对着 3g 的容器额度。

这里全部是证伪项：把预算换回宿主内存、或把 ``effective_concurrency`` 参数拿掉、
或把"份额不足"的抛错换回 ``max(512, ...)``，用例都会变红。
"""

from __future__ import annotations

from pathlib import Path

import psutil
import pytest
from antcode_worker import adaptive_limits as adaptive_mod
from antcode_worker import resource_budget
from antcode_worker.adaptive_limits import calculate_adaptive_limits
from antcode_worker.config import WorkerConfig, apply_resource_limits
from antcode_worker.resource_budget import TASK_POOL_SHARE_OF_BUDGET, ResourceBudgetError

from tests.unit.worker.cgroup_v2_support import simulate_cgroup_v2_host

_BYTES_PER_MIB = 1024 * 1024

# 真机实测环境：Worker 容器 mem_limit=3g / cpus=2，宿主 31.34GiB / 8 核
_CONTAINER_MEMORY_BYTES = 3 * 1024 * _BYTES_PER_MIB
_CONTAINER_CPU_MAX = "200000 100000"
_HOST_MEMORY_BYTES = 33651208192
_HOST_CPU_COUNT = 8

# 旧实现在上述环境下算出来的值，用作"必须不再出现"的负例
_LEGACY_HOST_DERIVED_MEMORY_MB = 2808
_LEGACY_HOST_DERIVED_CONCURRENCY = 8

_PINNED_CONCURRENCY = 4
_TINY_BUDGET_BYTES = 512 * _BYTES_PER_MIB
_MANUAL_OVERSELL_MEMORY_MB = 2048
_MANUAL_FITTING_MEMORY_MB = 512
_PROD_MEMORY_BYTES = 8 * 1024 * _BYTES_PER_MIB
_PROD_CPU_MAX = "400000 100000"


class _FakeVirtualMemory:
    def __init__(self, total: int) -> None:
        self.total = total


def _use_container_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    memory_bytes: int = _CONTAINER_MEMORY_BYTES,
    cpu_max: str = _CONTAINER_CPU_MAX,
) -> None:
    """把 cgroup 探测指向临时文件，模拟"跑在有额度的容器里"。"""
    memory_max = tmp_path / "memory.max"
    memory_max.write_text(str(memory_bytes), encoding="utf-8")
    cpu_file = tmp_path / "cpu.max"
    cpu_file.write_text(cpu_max, encoding="utf-8")
    simulate_cgroup_v2_host(monkeypatch, tmp_path)
    monkeypatch.setattr(resource_budget, "CGROUP_V2_MEMORY_MAX", memory_max)
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CPU_MAX", cpu_file)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _FakeVirtualMemory(_HOST_MEMORY_BYTES))
    monkeypatch.setattr(psutil, "cpu_count", lambda: _HOST_CPU_COUNT)


def _task_pool_mb(memory_bytes: int) -> int:
    return int(memory_bytes // _BYTES_PER_MIB * TASK_POOL_SHARE_OF_BUDGET)


def test_adaptive_limits_come_from_the_container_not_the_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3g 容器里算出的限额不得再等于按 31GB 宿主算出的 2808MB / 8 路。"""
    _use_container_budget(tmp_path, monkeypatch)

    limits = calculate_adaptive_limits()

    assert limits["task_memory_limit_mb"] != _LEGACY_HOST_DERIVED_MEMORY_MB
    assert limits["max_concurrent_tasks"] != _LEGACY_HOST_DERIVED_CONCURRENCY
    assert limits["task_memory_limit_mb"] <= _task_pool_mb(_CONTAINER_MEMORY_BYTES)


def test_adaptive_limits_never_oversell_the_container_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自适应路径按构造就不超卖：并发 × 限额 ≤ 任务池。"""
    _use_container_budget(tmp_path, monkeypatch)

    limits = calculate_adaptive_limits()
    required = limits["max_concurrent_tasks"] * limits["task_memory_limit_mb"]

    assert required <= _task_pool_mb(_CONTAINER_MEMORY_BYTES), (
        f"自适应值仍在超卖: {limits['max_concurrent_tasks']} × {limits['task_memory_limit_mb']}MB = {required}MB"
    )


def test_adaptive_memory_divides_by_the_pinned_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发被 env 钉死时，内存必须按**它**瓜分，而不是按自适应自己算的并发。"""
    _use_container_budget(tmp_path, monkeypatch)

    limits = calculate_adaptive_limits(_PINNED_CONCURRENCY)

    assert limits["max_concurrent_tasks"] == _PINNED_CONCURRENCY
    assert limits["task_memory_limit_mb"] == _task_pool_mb(_CONTAINER_MEMORY_BYTES) // _PINNED_CONCURRENCY
    assert _PINNED_CONCURRENCY * limits["task_memory_limit_mb"] <= _task_pool_mb(_CONTAINER_MEMORY_BYTES)


def test_production_defaults_fit_the_default_container_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产默认画像（mem_limit=8g / cpus=4，且不钉并发）必须自洽。

    终验算出的敞口是"默认 4 并发 × 2808MB = 11.2GB vs 8g"；生产 compose 其实
    根本没设 WORKER_MAX_CONCURRENT_TASKS，旧实现会取 8 路 → 22.5GB。
    """
    _use_container_budget(tmp_path, monkeypatch, memory_bytes=_PROD_MEMORY_BYTES, cpu_max=_PROD_CPU_MAX)

    limits = calculate_adaptive_limits()
    required = limits["max_concurrent_tasks"] * limits["task_memory_limit_mb"]

    assert required <= _task_pool_mb(_PROD_MEMORY_BYTES)


def test_adaptive_raises_when_the_budget_cannot_cover_the_pinned_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """份额低于最低可用限额时必须抛，而不是抬回 512MB 把超卖藏起来。"""
    _use_container_budget(tmp_path, monkeypatch, memory_bytes=_TINY_BUDGET_BYTES)

    with pytest.raises(ResourceBudgetError, match="内存预算不足"):
        calculate_adaptive_limits(_PINNED_CONCURRENCY)


def test_apply_resource_limits_passes_the_pinned_concurrency_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 钉死并发 + 内存走自适应，这个混合配置必须自洽（真机上就是它）。"""
    _use_container_budget(tmp_path, monkeypatch)

    applied = apply_resource_limits(WorkerConfig(max_concurrent_tasks=_PINNED_CONCURRENCY, auto_resource_limit=True))

    assert applied.max_concurrent_tasks == _PINNED_CONCURRENCY
    assert applied.task_memory_limit_mb == _task_pool_mb(_CONTAINER_MEMORY_BYTES) // _PINNED_CONCURRENCY


def test_apply_resource_limits_rejects_manual_oversell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto 关掉 = 运维接管了数值，超卖时唯一诚实的回应是拒绝启动。"""
    _use_container_budget(tmp_path, monkeypatch)

    with pytest.raises(ResourceBudgetError, match="超卖"):
        apply_resource_limits(
            WorkerConfig(
                max_concurrent_tasks=_PINNED_CONCURRENCY,
                task_memory_limit_mb=_MANUAL_OVERSELL_MEMORY_MB,
                auto_resource_limit=False,
            )
        )


def test_apply_resource_limits_refits_stale_persisted_value_under_auto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto 模式下持久化的旧超卖值必须被重算并告警，而不是拒绝启动。

    Worker 每次启动都把解析结果写回 worker_config.yaml，真机实证：从未被 API
    设过限额的 mn-worker-02/03 也带着上一版按宿主内存算出的 2808MB。直接抛会
    让整个集群在升级瞬间变成 0 容量。
    """
    _use_container_budget(tmp_path, monkeypatch)
    warnings: list[str] = []
    handler_id = adaptive_mod.logger.add(lambda message: warnings.append(str(message)), level="WARNING")

    try:
        applied = apply_resource_limits(
            WorkerConfig(
                max_concurrent_tasks=_PINNED_CONCURRENCY,
                task_memory_limit_mb=_LEGACY_HOST_DERIVED_MEMORY_MB,
                auto_resource_limit=True,
            )
        )
    finally:
        adaptive_mod.logger.remove(handler_id)

    assert applied.task_memory_limit_mb == _task_pool_mb(_CONTAINER_MEMORY_BYTES) // _PINNED_CONCURRENCY
    assert _PINNED_CONCURRENCY * applied.task_memory_limit_mb <= _task_pool_mb(_CONTAINER_MEMORY_BYTES)
    assert any("已按预算重算" in line for line in warnings), f"重算必须留下 WARNING，实际日志: {warnings}"


def test_apply_resource_limits_accepts_manual_values_that_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照组：手动值放得下就必须放行。"""
    _use_container_budget(tmp_path, monkeypatch)

    applied = apply_resource_limits(
        WorkerConfig(
            max_concurrent_tasks=_PINNED_CONCURRENCY,
            task_memory_limit_mb=_MANUAL_FITTING_MEMORY_MB,
            auto_resource_limit=False,
        )
    )

    assert applied.task_memory_limit_mb == _MANUAL_FITTING_MEMORY_MB


def test_host_sourced_budget_is_reported_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """探测不到 cgroup 时必须留下 WARNING——静默按宿主值算正是要修的 bug。"""
    # 裸机形态：连 cgroup 根都没有。挂着 cgroup 却不是 v2 是另一回事（直接抛），
    # 见 test_resource_budget_source.test_non_v2_cgroup_host_fails_loudly_for_memory。
    monkeypatch.setattr(resource_budget, "CGROUP_ROOT", tmp_path / "absent-root")
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CONTROLLERS", tmp_path / "absent-controllers")
    monkeypatch.setattr(resource_budget, "CGROUP_V2_MEMORY_MAX", tmp_path / "absent-v2-mem")
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CPU_MAX", tmp_path / "absent-v2-cpu")
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _FakeVirtualMemory(_HOST_MEMORY_BYTES))
    monkeypatch.setattr(psutil, "cpu_count", lambda: _HOST_CPU_COUNT)
    warnings: list[str] = []
    handler_id = adaptive_mod.logger.add(lambda message: warnings.append(str(message)), level="WARNING")

    try:
        calculate_adaptive_limits()
    finally:
        adaptive_mod.logger.remove(handler_id)

    assert any("内存预算取自宿主" in line for line in warnings), f"降级必须可见，实际日志: {warnings}"
