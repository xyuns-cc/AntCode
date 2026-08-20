"""自适应资源限额：从**本进程的预算**推导，而不是宿主 /proc 视图。

从 ``config.py`` 拆出，让后者只保留配置装载与合并。预算来源与 fail-closed
语义见 ``resource_budget.py``。
"""

from __future__ import annotations

import os

from loguru import logger

from antcode_worker.resource_budget import (
    BudgetSource,
    CpuBudget,
    MemoryBudget,
    ResourceBudgetError,
    resolve_cpu_budget,
    resolve_memory_budget,
    validate_capacity_fits_budget,
)

# 自适应定档常数（都从"预算"而非宿主视图推导，来源见 resource_budget.py）
_MB_PER_GIB = 1024
# 每个并发槽位至少要能分到 2GiB 预算才值得开出去，否则单任务限额会被摊得没法用。
_BUDGET_MB_PER_CONCURRENT_SLOT = 2 * _MB_PER_GIB
# 并发硬上限：更高的并发靠的是 IO 等待而不是本机资源，自适应不替运维做这个判断。
_MAX_ADAPTIVE_CONCURRENT_TASKS = 10
# 与 engine/config_update.py 的合法区间下界对齐：低于它的限额连运行时都起不来。
_MIN_ADAPTIVE_TASK_MEMORY_MB = 256
_MAX_ADAPTIVE_TASK_MEMORY_MB = 4096
_CPU_TIME_SECONDS_PER_CORE = 60
_MIN_ADAPTIVE_CPU_TIME_SEC = 300
_MAX_ADAPTIVE_CPU_TIME_SEC = 1800


def current_memory_budget() -> MemoryBudget:
    """本进程真正能用的内存预算（容器 cgroup 优先，裸机才是宿主总量）。"""
    import psutil

    return resolve_memory_budget(int(psutil.virtual_memory().total))


def calculate_adaptive_limits(effective_concurrency: int | None = None) -> dict[str, int]:
    """从**本进程的预算**自适应计算任务限制。

    Args:
        effective_concurrency: 已被显式固定（env/配置文件/本次 config_update）的
            并发数。固定了就必须按它瓜分内存池：否则会出现"内存按自适应的 8 路
            除出来、实际只跑 4 路"这种自相矛盾的组合——真机上就是这个状态，
            并发 env 固定 4 而单任务限额是按 8 除出来的 2808MB。

    算法：
    - max_concurrent_tasks: min(CPU 配额核数, 内存预算 / 2GiB, 10)
    - task_memory_limit_mb: 任务池 / 生效并发（任务池 = 预算 × 70%）
    - task_cpu_time_limit_sec: CPU 配额核数 × 60s，钳在 [300, 1800]
    """
    import psutil

    memory = current_memory_budget()
    cpu = resolve_cpu_budget(int(psutil.cpu_count() or os.cpu_count() or 1))
    _warn_on_host_sourced_budget(memory, cpu)

    concurrency = effective_concurrency or _adaptive_concurrency(memory, cpu)
    return {
        "max_concurrent_tasks": concurrency,
        "task_memory_limit_mb": _adaptive_task_memory_mb(memory, concurrency),
        "task_cpu_time_limit_sec": _adaptive_cpu_time_sec(cpu),
    }


def _warn_on_host_sourced_budget(memory: MemoryBudget, cpu: CpuBudget) -> None:
    """预算退到宿主视图时必须让运维看见，不能悄悄按宿主值算。

    裸机部署下宿主值就是正确答案，所以这不是失败；但容器里出现这条就说明
    cgroup 探测落空了，算出来的限额与容器额度无关，必须能被一眼看到。
    """
    if memory.source is BudgetSource.HOST:
        logger.warning("内存预算取自宿主而非 cgroup: {}", memory.describe())
    if cpu.source is BudgetSource.HOST:
        logger.warning("CPU 预算取自宿主而非 cgroup: {}", cpu.describe())


def _adaptive_concurrency(memory: MemoryBudget, cpu: CpuBudget) -> int:
    slots_by_memory = memory.total_mb // _BUDGET_MB_PER_CONCURRENT_SLOT
    return max(1, min(cpu.cores, slots_by_memory, _MAX_ADAPTIVE_CONCURRENT_TASKS))


def _adaptive_task_memory_mb(memory: MemoryBudget, concurrency: int) -> int:
    """任务池按生效并发均分；分不到最低可用限额就是预算真的不够，直接抛。

    旧实现在这里做 ``max(512, ...)``：份额不够时把值抬回 512MB，于是
    并发 × 限额悄悄超过容器额度。抬限额不解决问题，只是把超卖藏起来。
    """
    share_mb = memory.task_pool_mb // concurrency
    if share_mb < _MIN_ADAPTIVE_TASK_MEMORY_MB:
        raise ResourceBudgetError(
            f"内存预算不足以支撑 {concurrency} 路并发: 任务池 {memory.task_pool_mb}MB 均分后只有 "
            f"{share_mb}MB，低于最低可用限额 {_MIN_ADAPTIVE_TASK_MEMORY_MB}MB"
            f"（预算 {memory.describe()}）。请调小并发或提高容器 mem_limit。"
        )
    return min(_MAX_ADAPTIVE_TASK_MEMORY_MB, share_mb)


def _adaptive_cpu_time_sec(cpu: CpuBudget) -> int:
    return min(_MAX_ADAPTIVE_CPU_TIME_SEC, max(_MIN_ADAPTIVE_CPU_TIME_SEC, cpu.cores * _CPU_TIME_SECONDS_PER_CORE))


def fit_task_memory_to_budget(
    *,
    max_concurrent_tasks: int,
    task_memory_limit_mb: int,
    auto_resource_limit: bool,
) -> int:
    """返回真正放得进内存预算的单任务限额（启动期用）。

    auto 模式下，启动时读到的限额未必是运维写的：Worker 每次启动都会把解析结果
    写回 ``worker_config.yaml``（真机实证——从未被 API 设过限额的
    mn-worker-02/03 也带着上一版按宿主内存算出的 2808MB）。也就是说升级后
    **每台** Worker 都会以"手动值"的形态读回一个旧的超卖数字。容器额度是物理硬
    边界，这时按预算重算并**告警**；直接拒绝启动会把整个集群在升级瞬间打成 0 容量，
    比降级更糟。

    auto 关掉则相反：运维显式接管了数值，唯一诚实的回应是拒绝启动。
    """
    budget = current_memory_budget()
    try:
        validate_capacity_fits_budget(
            max_concurrent_tasks=max_concurrent_tasks,
            task_memory_limit_mb=task_memory_limit_mb,
            budget=budget,
        )
    except ResourceBudgetError:
        if not auto_resource_limit:
            raise
        fitted_mb = _adaptive_task_memory_mb(budget, max_concurrent_tasks)
        logger.warning(
            "单任务内存限额放不进内存预算，已按预算重算: {}MB -> {}MB (并发={}, 预算={})",
            task_memory_limit_mb,
            fitted_mb,
            max_concurrent_tasks,
            budget.describe(),
        )
        return fitted_mb
    return task_memory_limit_mb


# 安全默认值（当 auto_resource_limit=false 且值为 0 时使用）
DEFAULT_RESOURCE_LIMITS = {
    "max_concurrent_tasks": 4,
    "task_memory_limit_mb": 512,
    "task_cpu_time_limit_sec": 300,
}


__all__ = [
    "DEFAULT_RESOURCE_LIMITS",
    "calculate_adaptive_limits",
    "current_memory_budget",
    "fit_task_memory_to_budget",
]
