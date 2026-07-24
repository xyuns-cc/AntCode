"""Worker 统计 / 历史指标查询接口。

P2 拆分自 workers.py: 3 个纯查询 handler:
- GET /workers/stats (get_worker_stats)
- GET /workers/cluster/metrics/history (get_cluster_metrics_history)
- GET /workers/{worker_id}/metrics/history (get_worker_metrics_history)

_require_worker_access 由 register_stats_routes 时注入。契约 (URL / DI /
返回) 与旧实现一致。
"""

from __future__ import annotations

from antcode_core.application.services.workers import worker_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import UserRole
from antcode_core.domain.schemas.worker import WorkerAggregateStats
from fastapi import Depends, Query

from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success


async def get_worker_stats():
    stats = await worker_service.get_aggregate_stats()
    return success(stats)


async def get_cluster_metrics_history(hours: int):
    history = await worker_service.get_cluster_metrics_history(hours=hours)
    return success(history)


async def get_worker_metrics_history(worker_id: str, hours: int, current_user, *, require_worker_access):
    worker = await require_worker_access(worker_id, current_user)
    history = await worker_service.get_metrics_history(worker.id, hours=hours)
    return success(history)


def register_stats_routes(router, require_worker_access) -> None:
    admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))

    @router.get(
        "/stats",
        response_model=BaseResponse[WorkerAggregateStats],
        summary="获取 Worker 统计",
        description="获取所有 Worker 的聚合统计信息",
        dependencies=[admin_dep],
    )
    async def _get_worker_stats(current_user: TokenData = Depends(get_current_user)):
        _ = current_user
        return await get_worker_stats()

    @router.get(
        "/cluster/metrics/history",
        response_model=BaseResponse[dict],
        summary="获取集群历史指标",
        description="获取所有 Worker 的聚合历史指标",
        dependencies=[admin_dep],
    )
    async def _get_cluster_metrics_history(
        hours: int = Query(24, ge=1, le=720, description="查询时间范围（小时）"),
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await get_cluster_metrics_history(hours)

    @router.get(
        "/{worker_id}/metrics/history",
        response_model=BaseResponse[list],
        summary="获取 Worker 历史指标",
        description="获取 Worker 的历史指标数据用于图表展示",
    )
    async def _get_worker_metrics_history(
        worker_id: str,
        *,
        hours: int = Query(24, ge=1, le=720, description="查询时间范围（小时）"),
        current_user: TokenData = Depends(get_current_user),
    ):
        return await get_worker_metrics_history(
            worker_id, hours, current_user, require_worker_access=require_worker_access
        )


__all__ = [
    "get_cluster_metrics_history",
    "get_worker_metrics_history",
    "get_worker_stats",
    "register_stats_routes",
]
