"""预算必须来自容器自己的 cgroup，读不到就明确失败或明确降级——不许静默。

真机实测：``mem_limit=3g`` 的 Worker 容器里 ``/sys/fs/cgroup/memory.max`` 是
3221225472，而 ``psutil.virtual_memory().total`` 是 33651208192（宿主 31.34GiB），
差 10.4 倍；``cpu.max`` 是 2 CPU 而 ``nproc`` 是 8。

这里全部是证伪项：把 ``resolve_memory_budget`` / ``resolve_cpu_budget`` 换回直接
返回宿主值，或把 fail-closed 分支换回 ``return 宿主值``，用例会变红。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from antcode_worker import resource_budget
from antcode_worker.resource_budget import (
    BudgetSource,
    MemoryBudget,
    ResourceBudgetError,
    resolve_cpu_budget,
    resolve_memory_budget,
    validate_capacity_fits_budget,
)

_BYTES_PER_MIB = 1024 * 1024

# 真机实测值：容器 mem_limit=3g，宿主 31.34GiB
_CONTAINER_MEMORY_BYTES = 3 * 1024 * _BYTES_PER_MIB
_HOST_MEMORY_BYTES = 33651208192
_CONTAINER_MEMORY_MB = 3072
_HOST_MEMORY_MB = _HOST_MEMORY_BYTES // _BYTES_PER_MIB

# cgroup v1 不限制时写的哨兵值
_CGROUP_V1_UNLIMITED_SENTINEL = 9223372036854771712

# 真机实测值：cpu.max = "200000 100000" → 2 CPU，nproc = 8
_CONTAINER_CPU_MAX = "200000 100000"
_CONTAINER_CPU_CORES = 2
_HOST_CPU_COUNT = 8

# 超卖校验用例：任务池 = 3072 × 0.7 = 2150MB
_FITTING_CONCURRENCY = 4
_FITTING_MEMORY_MB = 537
_OVERSELL_MEMORY_MB = 2808


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _point_memory_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    v2: Path,
    v1: Path | None = None,
) -> None:
    monkeypatch.setattr(resource_budget, "CGROUP_V2_MEMORY_MAX", v2)
    monkeypatch.setattr(resource_budget, "CGROUP_V1_MEMORY_LIMIT", v1 or (v2.parent / "absent-v1"))


def test_memory_budget_prefers_cgroup_over_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有 cgroup 上限时必须用它，宿主总量是错的来源。"""
    _point_memory_paths(monkeypatch, v2=_write(tmp_path / "memory.max", str(_CONTAINER_MEMORY_BYTES)))

    budget = resolve_memory_budget(_HOST_MEMORY_BYTES)

    assert budget.source is BudgetSource.CGROUP_V2
    assert budget.total_mb == _CONTAINER_MEMORY_MB
    assert budget.total_mb != _HOST_MEMORY_MB, "读到宿主内存说明 cgroup 没有被采信"


def test_memory_budget_falls_back_to_host_only_when_no_cgroup_limit_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cgroup 文件都不存在 = 裸机部署，宿主总量就是正确答案，但来源必须写明。"""
    _point_memory_paths(monkeypatch, v2=tmp_path / "absent-v2")

    budget = resolve_memory_budget(_HOST_MEMORY_BYTES)

    assert budget.source is BudgetSource.HOST
    assert budget.total_bytes == _HOST_MEMORY_BYTES
    assert "已探测" in budget.origin, "降级到宿主值时必须写清探测过哪些路径"


@pytest.mark.parametrize("raw", ["max", str(_CGROUP_V1_UNLIMITED_SENTINEL)])
def test_unlimited_cgroup_value_means_host_budget(
    raw: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max``（v2）与哨兵值（v1）都表示没设限，此时宿主总量才是真预算。"""
    _point_memory_paths(monkeypatch, v2=_write(tmp_path / "memory.max", raw))

    budget = resolve_memory_budget(_HOST_MEMORY_BYTES)

    assert budget.source is BudgetSource.HOST
    assert budget.total_bytes == _HOST_MEMORY_BYTES
    assert "未设限" in budget.origin or "已探测" in budget.origin


def test_unreadable_cgroup_file_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """文件在但读不出来 = 预算不可知，必须抛，绝不退回宿主值。"""
    unreadable = tmp_path / "memory.max"
    unreadable.mkdir()  # 目录：read_text 抛 IsADirectoryError（OSError 子类）
    _point_memory_paths(monkeypatch, v2=unreadable)

    with pytest.raises(ResourceBudgetError, match="读取失败"):
        resolve_memory_budget(_HOST_MEMORY_BYTES)


def test_unparseable_cgroup_value_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """内容解析不了同样是预算不可知，不许按宿主内存估算。"""
    _point_memory_paths(monkeypatch, v2=_write(tmp_path / "memory.max", "不是数字"))

    with pytest.raises(ResourceBudgetError, match="拒绝按宿主内存估算"):
        resolve_memory_budget(_HOST_MEMORY_BYTES)


def test_cgroup_v1_is_used_when_v2_is_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """老内核只有 v1 层级，同样必须被采信。"""
    _point_memory_paths(
        monkeypatch,
        v2=tmp_path / "absent-v2",
        v1=_write(tmp_path / "memory.limit_in_bytes", str(_CONTAINER_MEMORY_BYTES)),
    )

    budget = resolve_memory_budget(_HOST_MEMORY_BYTES)

    assert budget.source is BudgetSource.CGROUP_V1
    assert budget.total_mb == _CONTAINER_MEMORY_MB


def test_cpu_budget_prefers_cgroup_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """并发数是按核数定的，核数也必须读 cgroup 配额而不是 nproc。"""
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CPU_MAX", _write(tmp_path / "cpu.max", _CONTAINER_CPU_MAX))
    monkeypatch.setattr(resource_budget, "CGROUP_V1_CPU_QUOTA", tmp_path / "absent-quota")

    budget = resolve_cpu_budget(_HOST_CPU_COUNT)

    assert budget.source is BudgetSource.CGROUP_V2
    assert budget.cores == _CONTAINER_CPU_CORES
    assert budget.cores != _HOST_CPU_COUNT, "读到宿主核数说明 cgroup 配额没被采信"


def test_cpu_budget_without_quota_uses_host_cores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """对照组：没有 CPU 配额时宿主核数就是正确答案。"""
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CPU_MAX", _write(tmp_path / "cpu.max", "max 100000"))
    monkeypatch.setattr(resource_budget, "CGROUP_V1_CPU_QUOTA", tmp_path / "absent-quota")

    budget = resolve_cpu_budget(_HOST_CPU_COUNT)

    assert budget.source is BudgetSource.HOST
    assert budget.cores == _HOST_CPU_COUNT


def test_unparseable_cpu_quota_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CPU_MAX", _write(tmp_path / "cpu.max", "garbage"))

    with pytest.raises(ResourceBudgetError, match="拒绝按宿主核数估算"):
        resolve_cpu_budget(_HOST_CPU_COUNT)


def _container_budget() -> MemoryBudget:
    return MemoryBudget(
        total_bytes=_CONTAINER_MEMORY_BYTES,
        source=BudgetSource.CGROUP_V2,
        origin="test",
    )


def test_validate_rejects_capacity_that_oversells_the_budget() -> None:
    """并发 × 限额超出任务池必须显式抛——这是终验算出的 11.2GB vs 3g 敞口。"""
    with pytest.raises(ResourceBudgetError, match="超卖"):
        validate_capacity_fits_budget(
            max_concurrent_tasks=_FITTING_CONCURRENCY,
            task_memory_limit_mb=_OVERSELL_MEMORY_MB,
            budget=_container_budget(),
        )


def test_validate_accepts_capacity_that_fits() -> None:
    """对照组：放得下的组合必须放行，否则"全过"和"全挂"长得一样。"""
    validate_capacity_fits_budget(
        max_concurrent_tasks=_FITTING_CONCURRENCY,
        task_memory_limit_mb=_FITTING_MEMORY_MB,
        budget=_container_budget(),
    )
