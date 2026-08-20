"""Worker 资源限制查询 / 调整接口 (管理员)。

P2 拆分自 workers.py:
- GET /workers/{worker_id}/resources (get_worker_resources)
- POST /workers/{worker_id}/resources (update_worker_resources)

契约 (URL / DI / 返回结构 / 参数校验范围) 与旧实现一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from antcode_core.application.services.workers import worker_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import User
from antcode_core.infrastructure.redis import (
    build_config_update_control_payload,
    control_stream,
    get_redis_client,
)
from fastapi import Body, Depends, HTTPException, Request, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1.mutation_audit import audit_worker_resources_updated

# 参数验证范围
_MAX_CONCURRENT_MIN, _MAX_CONCURRENT_MAX = 1, 20
_MEMORY_LIMIT_MIN_MB, _MEMORY_LIMIT_MAX_MB = 256, 8192
_CPU_LIMIT_MIN_SEC, _CPU_LIMIT_MAX_SEC = 60, 3600
BYTES_PER_MIB = 1024 * 1024
BYTES_PER_GIB = 1024 * BYTES_PER_MIB


@dataclass(frozen=True, slots=True)
class CapacityMetric:
    byte_field: str
    legacy_field: str
    divisor: int


MEMORY_USED = CapacityMetric("memoryUsed", "memory_used_mb", BYTES_PER_MIB)
MEMORY_TOTAL = CapacityMetric("memoryTotal", "memory_total_mb", BYTES_PER_MIB)
DISK_USED = CapacityMetric("diskUsed", "disk_used_gb", BYTES_PER_GIB)
DISK_TOTAL = CapacityMetric("diskTotal", "disk_total_gb", BYTES_PER_GIB)

LIMIT_FIELDS = ("max_concurrent_tasks", "task_memory_limit_mb", "task_cpu_time_limit_sec")
# 心跳落库后的驼峰键，以及归一化前的下划线别名。三项都由 Worker 上报生效值
# (contracts/proto/common.proto 的 Metrics: 5 / 20 / 21 号字段)。
_EFFECTIVE_LIMIT_KEYS: dict[str, tuple[str, ...]] = {
    "max_concurrent_tasks": ("maxConcurrentTasks", "max_concurrent_tasks"),
    "task_memory_limit_mb": ("taskMemoryLimitMb", "task_memory_limit_mb"),
    "task_cpu_time_limit_sec": ("taskCpuTimeLimitSec", "task_cpu_time_limit_sec"),
}


async def _require_admin(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限查看资源配置")
    return user


async def _require_super_admin(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要超级管理员权限修改资源配置")
    return user


def _to_float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, str | int | float):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _capacity_value(resources: dict, metric: CapacityMetric) -> float:
    if resources.get(metric.byte_field) not in (None, ""):
        return round(_to_float(resources[metric.byte_field]) / metric.divisor, 1)
    return _to_float(resources.get(metric.legacy_field, 0))


def _reported_limit(resources: dict, keys: tuple[str, ...]) -> int | None:
    """只认心跳上报过的正整数真值。

    0 表示"没有限额在生效"，与"没上报"一样都不是可展示的配额，统一如实为 None。
    """
    for key in keys:
        value = resources.get(key)
        # bool 是 int 的子类，但 True 不是限额。
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _effective_limits(resources: dict) -> dict[str, int | None]:
    """生效限额只有执行面知道，控制面推导不出来。

    Worker 的限额由它自己的 cgroup 预算算出，并在启动与 config_update 时按预算
    重算收敛（services/worker/src/antcode_worker/adaptive_limits.py）。web-api 的
    settings 是控制面自己的配置，与某台 Worker 的生效值没有因果关系——拿它兜底
    等于编造一个 API 并不知道的数字，且真机上恒偏大（10 vs 4、600s vs 480s）。
    所以这里只认心跳上报过的真值，其余如实为 None。
    """
    return {name: _reported_limit(resources, keys) for name, keys in _EFFECTIVE_LIMIT_KEYS.items()}


def _configured_limits(limits: dict) -> dict[str, int | None]:
    """控制面下发过的值。下发成功不等于生效，Worker 可能已按预算收敛或拒绝。"""
    return {name: limits.get(name) for name in LIMIT_FIELDS}


async def get_worker_resources(worker_id: str, current_user: TokenData):
    await _require_admin(current_user)
    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    resources = worker.metrics if isinstance(worker.metrics, dict) else {}
    limits = worker.resource_limits if isinstance(worker.resource_limits, dict) else {}

    return success(
        {
            "limits": _effective_limits(resources),
            "configured_limits": _configured_limits(limits),
            "auto_adjustment": limits.get("auto_resource_limit", True),
            "resource_stats": {
                "cpu_percent": round(_to_float(resources.get("cpu", resources.get("cpu_percent", 0))), 1),
                "memory_percent": round(_to_float(resources.get("memory", resources.get("memory_percent", 0))), 1),
                "disk_percent": round(_to_float(resources.get("disk", resources.get("disk_percent", 0))), 1),
                "memory_used_mb": _capacity_value(resources, MEMORY_USED),
                "memory_total_mb": _capacity_value(resources, MEMORY_TOTAL),
                "disk_used_gb": _capacity_value(resources, DISK_USED),
                "disk_total_gb": _capacity_value(resources, DISK_TOTAL),
                "running_tasks": resources.get("runningTasks", resources.get("running_tasks", 0)),
                "queued_tasks": resources.get("queuedTasks", resources.get("queued_tasks", 0)),
                "uptime_seconds": resources.get("uptime", resources.get("uptime_seconds", 0)),
            },
        }
    )


def _validate_resource_params(request: dict) -> tuple[dict, dict]:
    """校验参数范围并返回 (config_params_for_redis, config_for_db)。"""
    max_concurrent = request.get("max_concurrent_tasks")
    memory_limit = request.get("task_memory_limit_mb")
    cpu_limit = request.get("task_cpu_time_limit_sec")
    auto_limit = request.get("auto_resource_limit")

    if max_concurrent is not None and not (_MAX_CONCURRENT_MIN <= max_concurrent <= _MAX_CONCURRENT_MAX):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"最大并发任务数必须在 {_MAX_CONCURRENT_MIN}-{_MAX_CONCURRENT_MAX} 之间",
        )
    if memory_limit is not None and not (_MEMORY_LIMIT_MIN_MB <= memory_limit <= _MEMORY_LIMIT_MAX_MB):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单任务内存限制必须在 {_MEMORY_LIMIT_MIN_MB}-{_MEMORY_LIMIT_MAX_MB} MB 之间",
        )
    if cpu_limit is not None and not (_CPU_LIMIT_MIN_SEC <= cpu_limit <= _CPU_LIMIT_MAX_SEC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单任务CPU时间限制必须在 {_CPU_LIMIT_MIN_SEC}-{_CPU_LIMIT_MAX_SEC} 秒之间",
        )

    redis_params: dict[str, str] = {}
    db_params: dict = {}
    if max_concurrent is not None:
        redis_params["max_concurrent_tasks"] = str(max_concurrent)
        db_params["max_concurrent_tasks"] = max_concurrent
    if memory_limit is not None:
        redis_params["task_memory_limit_mb"] = str(memory_limit)
        db_params["task_memory_limit_mb"] = memory_limit
    if cpu_limit is not None:
        redis_params["task_cpu_time_limit_sec"] = str(cpu_limit)
        db_params["task_cpu_time_limit_sec"] = cpu_limit
    if auto_limit is not None:
        redis_params["auto_resource_limit"] = str(auto_limit).lower()
        db_params["auto_resource_limit"] = auto_limit

    if not redis_params:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要提供一个配置项")

    return redis_params, db_params


async def update_worker_resources(worker_id: str, request: dict, current_user: TokenData, *, http_request: Request):
    user = await _require_super_admin(current_user)
    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    config_params, db_params = _validate_resource_params(request)

    # 先快照旧限额：worker.resource_limits 是原地 update，落库后就再也取不到调整前的值。
    previous_limits = dict(worker.resource_limits or {})
    if worker.resource_limits is None:
        worker.resource_limits = {}
    worker.resource_limits.update(db_params)
    await worker.save()

    synced = False
    try:
        from antcode_core.application.services.runtime.runtime_control_service import (
            write_control_event,
        )

        redis = await get_redis_client()
        payload = build_config_update_control_payload(config_params)
        # P2-24: 走 write_control_event 带 maxlen 近似裁剪, 避免 control:{worker_id}
        # stream 无限增长。
        await write_control_event(redis, control_stream(worker.public_id), payload)
        synced = True
    except Exception as e:  # noqa: BLE001 - Redis 抖动不阻塞 DB 更新
        logger.warning(f"发送配置更新失败: {e}")

    logger.info(f"超级管理员 {user.username} 调整了 Worker {worker_id} 的资源限制: {config_params}")
    await audit_worker_resources_updated(
        http_request,
        current_user,
        worker,
        old_value=previous_limits,
        new_value={"limits": dict(db_params), "synced": synced},
    )

    # 不说"已更新": 控制面下发的是**请求**。Worker 按自己的 cgroup 预算校验，
    # 超卖时会重算收敛（auto）或直接拒绝（手动），真机上就出现过 API 返回 200
    # 而 Worker 侧抛 ResourceBudgetError 的组合。生效值以下一次心跳为准。
    return success({"updated": config_params, "synced": synced}, message="资源限制已下发，生效值以 Worker 上报为准")


def register_resources_routes(router) -> None:
    @router.get(
        "/{worker_id}/resources",
        response_model=BaseResponse[dict],
        summary="获取 Worker 资源限制",
        description="获取指定 Worker 的资源限制和监控状态（需要管理员权限）",
    )
    async def _get_worker_resources(worker_id: str, current_user: TokenData = Depends(get_current_user)):
        return await get_worker_resources(worker_id, current_user)

    @router.post(
        "/{worker_id}/resources",
        response_model=BaseResponse[dict],
        summary="调整 Worker 资源限制",
        description="手动调整指定 Worker 的资源限制（仅超级管理员）",
    )
    async def _update_worker_resources(
        worker_id: str,
        request: dict = Body(...),
        current_user: TokenData = Depends(get_current_user),
        *,
        http_request: Request,
    ):
        return await update_worker_resources(worker_id, request, current_user, http_request=http_request)


__all__ = [
    "get_worker_resources",
    "register_resources_routes",
    "update_worker_resources",
]
