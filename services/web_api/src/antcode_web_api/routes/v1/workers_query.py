"""Worker 只读查询接口 (load ranking / best worker / render-capable list)。

P2 拆分自 workers.py:3 个纯查询 handler, 都只依赖 worker_service /
worker_load_balancer / Worker model + 主 workers.py 传入的
_worker_to_response 转换器。

保持对外契约:
- workers_route.get_workers_load_ranking / get_best_worker /
  get_render_capable_workers 通过 workers.py re-export 继续可命中。
- URL / DI / 响应 shape 完全不变。
"""

from __future__ import annotations

from typing import Any

from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import UserRole, WorkerStatus
from antcode_core.domain.schemas.worker import WorkerListResponse
from fastapi import Depends, Query
from tortoise.expressions import Q

from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success


async def get_workers_load_ranking(
    *,
    region: str | None,
    top_n: int,
) -> Any:
    from antcode_core.application.services.workers import worker_load_balancer

    rankings = await worker_load_balancer.get_workers_ranking(region=region, top_n=top_n)
    return success(rankings)


async def get_best_worker(
    *,
    region: str | None,
    tags: str | None,
    require_render: bool,
    worker_to_response,
) -> Any:
    from antcode_core.application.services.workers import worker_load_balancer

    tag_list = tags.split(",") if tags else None

    best_worker = await worker_load_balancer.select_best_worker(
        region=region, tags=tag_list, require_render=require_render
    )
    if not best_worker:
        return success(
            {
                "available": False,
                "message": "没有可用的渲染 Worker" if require_render else "没有可用的 Worker",
            }
        )
    score = worker_load_balancer.calculate_load_score(best_worker)
    return success(
        {
            "available": True,
            "worker": worker_to_response(best_worker).model_dump(),
            "load_score": score,
        }
    )


async def get_render_capable_workers(
    *,
    page: int,
    size: int,
    region: str | None,
    worker_to_response,
) -> Any:
    from antcode_core.domain.models import Worker

    query = Worker.filter(status=WorkerStatus.ONLINE.value)
    if region:
        query = query.filter(region=region)
    query = query.filter(
        Q(capabilities__contains={"task_types": ["render"]})
        | Q(capabilities__contains={"playwright": {"enabled": True}})
    )
    total = await query.count()
    offset = (page - 1) * size
    workers = await query.offset(offset).limit(size)
    items = [worker_to_response(worker) for worker in workers]
    return success(WorkerListResponse(items=items, total=total, page=page, size=size))


def register_query_routes(router, worker_to_response) -> None:
    """把 3 个查询接口挂到主 router (由 workers.py 顶层调用)。

    传入 worker_to_response 转换器,避免循环 import;URL / DI 完全同旧实现。
    """
    admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))

    @router.get(
        "/load/ranking",
        response_model=BaseResponse[list],
        summary="获取 Worker 负载排名",
        description="获取所有在线 Worker 的负载排名，用于任务分发决策",
        dependencies=[admin_dep],
    )
    async def _get_workers_load_ranking(
        region: str | None = Query(None, description="区域过滤"),
        top_n: int = Query(10, ge=1, le=50, description="返回前N个 Worker"),
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await get_workers_load_ranking(region=region, top_n=top_n)

    @router.get(
        "/best",
        response_model=BaseResponse[dict],
        summary="获取最佳 Worker",
        description="根据负载自动选择最适合执行任务的 Worker",
        dependencies=[admin_dep],
    )
    async def _get_best_worker(
        *,
        region: str | None = Query(None, description="区域过滤"),
        tags: str | None = Query(None, description="标签过滤，逗号分隔"),
        require_render: bool = Query(False, description="是否需要渲染能力"),
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await get_best_worker(
            region=region,
            tags=tags,
            require_render=require_render,
            worker_to_response=worker_to_response,
        )

    @router.get(
        "/render-capable",
        response_model=BaseResponse[WorkerListResponse],
        summary="获取有渲染能力的 Worker",
        description="获取所有具有浏览器渲染能力（DrissionPage）的在线 Worker",
        dependencies=[admin_dep],
    )
    async def _get_render_capable_workers(
        *,
        page: int = Query(1, ge=1, description="页码"),
        size: int = Query(20, ge=1, le=100, description="每页数量"),
        region: str | None = Query(None, description="区域过滤"),
        current_user: TokenData = Depends(get_current_user),
    ):
        _ = current_user
        return await get_render_capable_workers(
            page=page,
            size=size,
            region=region,
            worker_to_response=worker_to_response,
        )


__all__ = [
    "get_best_worker",
    "get_render_capable_workers",
    "get_workers_load_ranking",
    "register_query_routes",
]
