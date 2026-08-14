"""Worker selection helpers shared by dispatcher entry points."""

from __future__ import annotations

from typing import Any

from loguru import logger

from antcode_core.application.services.workers.worker_capability_routing import (
    has_render_capability,
    resolve_capability_map,
    supports_task_types,
)
from antcode_core.application.services.workers.worker_registration_gate import (
    has_unacknowledged_v2_registration,
)
from antcode_core.domain.models import Worker, WorkerStatus


async def select_dispatch_worker(
    load_balancer: Any,
    *,
    worker_id: str | int | None,
    region: str | None,
    tags: list[str] | None,
    require_render: bool,
    require_task_type: str | frozenset[str] | None,
) -> Worker | None:
    if worker_id is None:
        return await load_balancer.select_best_worker(
            region=region,
            tags=tags,
            require_render=require_render,
            require_task_type=require_task_type,
        )
    worker = await _load_worker(worker_id)
    return await _validate_specific_worker(
        worker,
        requested_id=worker_id,
        region=region,
        require_render=require_render,
        require_task_type=require_task_type,
    )


async def _load_worker(worker_id: str | int) -> Worker | None:
    worker = await Worker.filter(public_id=str(worker_id)).first()
    if worker is not None:
        return worker
    try:
        internal_id = int(worker_id)
    except (TypeError, ValueError):
        return None
    return await Worker.filter(id=internal_id).first()


async def _validate_specific_worker(
    worker: Worker | None,
    *,
    requested_id: str | int,
    region: str | None,
    require_render: bool,
    require_task_type: str | frozenset[str] | None,
) -> Worker | None:
    if worker is None:
        logger.warning(f"Worker 不存在: {requested_id}")
        return None
    if worker.status != WorkerStatus.ONLINE:
        logger.warning(f"节点离线: {worker.name}")
        return None
    if await has_unacknowledged_v2_registration(worker.public_id):
        logger.warning(f"指定 Worker [{worker.name}] 尚未确认 V2 注册")
        return None
    if region and worker.region != region:
        logger.warning(
            "指定 Worker [{}] 区域不匹配: required={} actual={}",
            worker.name,
            region,
            worker.region,
        )
        return None
    return await _validate_worker_capabilities(
        worker,
        require_render=require_render,
        require_task_type=require_task_type,
    )


async def _validate_worker_capabilities(
    worker: Worker,
    *,
    require_render: bool,
    require_task_type: str | frozenset[str] | None,
) -> Worker | None:
    capabilities = await resolve_capability_map(
        [worker],
        authoritative=bool(require_render or require_task_type),
    )
    if require_render and not has_render_capability(capabilities[worker.id]):
        logger.warning(f"指定 Worker [{worker.name}] 无渲染能力")
        return None
    if require_task_type and not supports_task_types(capabilities[worker.id], require_task_type):
        logger.warning(f"指定 Worker [{worker.name}] 不支持 task_type={require_task_type!r}")
        return None
    return worker


__all__ = ["select_dispatch_worker"]
