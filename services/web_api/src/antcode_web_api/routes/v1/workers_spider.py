"""Worker spider stats 查询接口。

P2 拆分自 workers.py: 3 个纯查询 handler:
- GET /workers/stats/spider (cluster 聚合)
- GET /workers/{worker_id}/stats/spider
- GET /workers/{worker_id}/stats/spider/history

依赖只 spider_stats_service + worker_service, 都是简单查询。
"""

from __future__ import annotations

from antcode_core.application.services.workers import worker_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import UserRole
from fastapi import Depends, HTTPException, Query, status

from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success


async def get_cluster_spider_stats():
    from antcode_core.application.services.workers.spider_stats_service import spider_stats_service

    stats = await spider_stats_service.get_cluster_spider_stats()
    return success(stats)


async def get_worker_spider_stats(worker_id: str):
    from antcode_core.application.services.workers.spider_stats_service import spider_stats_service

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    stats = await spider_stats_service.get_worker_spider_stats(worker.id)
    return success(stats.model_dump())


async def get_worker_spider_stats_history(worker_id: str, hours: int):
    from antcode_core.application.services.workers.spider_stats_service import spider_stats_service

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    history = await spider_stats_service.get_spider_stats_history(worker_id=worker.id, hours=hours)
    return success(history)


def register_spider_routes(router) -> None:
    admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))

    @router.get(
        "/stats/spider",
        response_model=BaseResponse[dict],
        summary="获取集群爬虫统计",
        description="获取所有在线 Worker 的爬虫统计聚合数据",
        dependencies=[admin_dep],
    )
    async def _get_cluster_spider_stats(current_user: TokenData = Depends(get_current_user)):
        _ = current_user
        return await get_cluster_spider_stats()

    @router.get(
        "/{worker_id}/stats/spider",
        response_model=BaseResponse[dict],
        summary="获取单 Worker 爬虫统计",
        description="获取指定 Worker 的爬虫统计数据",
        dependencies=[admin_dep],
    )
    async def _get_worker_spider_stats(
        worker_id: str,
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await get_worker_spider_stats(worker_id)

    @router.get(
        "/{worker_id}/stats/spider/history",
        response_model=BaseResponse[list],
        summary="获取 Worker 爬虫统计历史",
        description="获取指定 Worker 的爬虫统计历史趋势数据",
        dependencies=[admin_dep],
    )
    async def _get_worker_spider_stats_history(
        worker_id: str,
        hours: int = Query(1, ge=1, le=24, description="查询时间范围（小时）"),
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await get_worker_spider_stats_history(worker_id, hours)


__all__ = [
    "get_cluster_spider_stats",
    "get_worker_spider_stats",
    "get_worker_spider_stats_history",
    "register_spider_routes",
]
