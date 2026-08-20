"""Validation for live Worker resource configuration updates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from antcode_worker.adaptive_limits import current_memory_budget
from antcode_worker.resource_budget import validate_capacity_fits_budget

MAX_CONCURRENT_TASKS_MIN = 1
MAX_CONCURRENT_TASKS_MAX = 20
TASK_MEMORY_LIMIT_MB_MIN = 256
TASK_MEMORY_LIMIT_MB_MAX = 8192
TASK_CPU_TIME_LIMIT_SEC_MIN = 60
TASK_CPU_TIME_LIMIT_SEC_MAX = 3600

_RESOURCE_FIELDS = {
    "max_concurrent_tasks",
    "task_memory_limit_mb",
    "task_cpu_time_limit_sec",
}
_AUTO_RESOURCE_FIELD = "auto_resource_limit"


@dataclass(frozen=True)
class EngineConfigUpdate:
    """Validated, immutable resource update."""

    max_concurrent_tasks: int | None = None
    task_memory_limit_mb: int | None = None
    task_cpu_time_limit_sec: int | None = None
    auto_resource_limit: bool | None = None


@dataclass(frozen=True)
class CurrentCapacity:
    """引擎当前生效的容量，用来补齐 payload 里没带的那一半。"""

    max_concurrent_tasks: int
    task_memory_limit_mb: int


def resolve_engine_config_update(
    config: dict[str, Any],
    provider: Callable[[int | None], dict[str, int]] | None,
    current: CurrentCapacity,
) -> EngineConfigUpdate:
    """校验 → 补齐自适应值 → 确认生效组合放得进内存预算，三步一起做完。

    控制台单改 ``task_memory_limit_mb`` 时并发不在 payload 里，生效并发是引擎
    当前的值——只校验 payload 会漏掉"单改内存把总量顶爆"这条路径。任何一步失败
    都在引擎改状态之前抛出，配置不会半生效。
    """
    update = resolve_adaptive_update(parse_engine_config_update(config), provider)
    validate_capacity_fits_budget(
        max_concurrent_tasks=update.max_concurrent_tasks or current.max_concurrent_tasks,
        task_memory_limit_mb=update.task_memory_limit_mb or current.task_memory_limit_mb,
        budget=current_memory_budget(),
    )
    return update


def parse_engine_config_update(config: dict[str, Any]) -> EngineConfigUpdate:
    """Validate the complete update before the Engine mutates live state."""
    if not isinstance(config, dict):
        raise ValueError("config_update payload 必须是 object")
    unknown = set(config) - _RESOURCE_FIELDS - {_AUTO_RESOURCE_FIELD}
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise ValueError(f"config_update 包含不支持的字段: {names}")
    update = EngineConfigUpdate(
        max_concurrent_tasks=_bounded_int(
            config,
            "max_concurrent_tasks",
            minimum=MAX_CONCURRENT_TASKS_MIN,
            maximum=MAX_CONCURRENT_TASKS_MAX,
        ),
        task_memory_limit_mb=_bounded_int(
            config,
            "task_memory_limit_mb",
            minimum=TASK_MEMORY_LIMIT_MB_MIN,
            maximum=TASK_MEMORY_LIMIT_MB_MAX,
        ),
        task_cpu_time_limit_sec=_bounded_int(
            config,
            "task_cpu_time_limit_sec",
            minimum=TASK_CPU_TIME_LIMIT_SEC_MIN,
            maximum=TASK_CPU_TIME_LIMIT_SEC_MAX,
        ),
        auto_resource_limit=_optional_bool(config, _AUTO_RESOURCE_FIELD),
    )
    if not any(value is not None for value in update.__dict__.values()):
        raise ValueError("config_update 未包含可应用的资源字段")
    return update


def resolve_adaptive_update(
    update: EngineConfigUpdate,
    provider: Callable[[int | None], dict[str, int]] | None,
) -> EngineConfigUpdate:
    """Fill omitted numeric fields when this event explicitly enables auto mode.

    本次事件如果同时钉死了并发，就必须把它交给自适应计算器：内存池要按**生效**
    并发瓜分，否则会算出"内存按 N 路、实际跑 M 路"的自相矛盾组合。
    """
    if update.auto_resource_limit is not True or _has_all_resource_values(update):
        return update
    if provider is None:
        raise RuntimeError("Worker 未配置自适应资源计算器")
    adaptive = parse_engine_config_update(provider(update.max_concurrent_tasks))
    _require_complete_adaptive_values(adaptive)
    return EngineConfigUpdate(
        max_concurrent_tasks=update.max_concurrent_tasks or adaptive.max_concurrent_tasks,
        task_memory_limit_mb=update.task_memory_limit_mb or adaptive.task_memory_limit_mb,
        task_cpu_time_limit_sec=update.task_cpu_time_limit_sec or adaptive.task_cpu_time_limit_sec,
        auto_resource_limit=True,
    )


def _bounded_int(
    config: dict[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if key not in config:
        return None
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"{key} 必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是整数") from exc
    if isinstance(value, str) and value.strip() != str(parsed):
        raise ValueError(f"{key} 必须是十进制整数")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{key} 必须在 {minimum}-{maximum} 之间")
    return parsed


def _optional_bool(config: dict[str, Any], key: str) -> bool | None:
    if key not in config:
        return None
    value = config[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{key} 必须是布尔值")


def _has_all_resource_values(update: EngineConfigUpdate) -> bool:
    return all(
        value is not None
        for value in (
            update.max_concurrent_tasks,
            update.task_memory_limit_mb,
            update.task_cpu_time_limit_sec,
        )
    )


def _require_complete_adaptive_values(update: EngineConfigUpdate) -> None:
    if not _has_all_resource_values(update):
        raise ValueError("自适应资源计算器必须返回全部资源字段")


__all__ = [
    "CurrentCapacity",
    "EngineConfigUpdate",
    "parse_engine_config_update",
    "resolve_adaptive_update",
    "resolve_engine_config_update",
]
