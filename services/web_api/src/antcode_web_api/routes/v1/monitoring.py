from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from antcode_core.application.services.monitoring import monitoring_service
from antcode_core.application.services.workers.worker_service import worker_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import User, UserRole, Worker
from antcode_core.domain.schemas.monitoring import (
    ClusterSummaryResponse,
    WorkerHistoryItem,
    WorkerHistoryQueryResponse,
    WorkerRealtimePoint,
    WorkerRealtimeResponse,
    WorkerSpiderStats,
    WorkerStatus,
    WorkerSummary,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status

from antcode_web_api.deps import require_role

router = APIRouter()


async def _ensure_authenticated_user(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或会话已失效")
    return user


async def _ensure_worker_access(worker_id: str, current_user: TokenData) -> User:
    user = await _ensure_authenticated_user(current_user)
    worker = await Worker.get_or_none(public_id=worker_id)
    allowed = worker and await worker_service.check_user_worker_permission(
        user_id=user.id,
        worker_id=worker.id,
        is_admin=user.is_admin,
        required_permission="view",
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return user


def _convert_status(raw):
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None or value == "":
            result[key] = None
            continue
        try:
            if key in {"cpu_percent", "memory_percent", "disk_percent"} or key in {
                "network_sent_mb",
                "network_recv_mb",
            }:
                result[key] = float(value)
            elif key in {"memory_used_mb", "uptime_seconds", "update_time"}:
                result[key] = int(value)
            else:
                result[key] = value
        except (TypeError, ValueError):
            result[key] = value
    return result


def _convert_spider(raw):
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None or value == "":
            result[key] = None
            continue
        try:
            result[key] = int(value)
        except (TypeError, ValueError):
            try:
                result[key] = float(value)
            except (TypeError, ValueError):
                result[key] = value
    return result


@router.get("/workers", response_model=list[WorkerSummary], summary="列出在线 Worker")
async def list_online_workers(current_user: TokenData = Depends(get_current_user)):
    user = await _ensure_authenticated_user(current_user)
    workers = await monitoring_service.get_online_workers()
    if not user.is_admin:
        allowed_workers = await worker_service.get_user_workers(user.id, is_admin=False)
        allowed_ids = {worker.public_id for worker in allowed_workers}
        workers = [worker for worker in workers if worker.get("worker_id") in allowed_ids]
    summaries = []
    for worker in workers:
        status = WorkerStatus(**_convert_status(worker.get("status", {})))
        spider = WorkerSpiderStats(**_convert_spider(worker.get("spider", {})))
        summaries.append(
            WorkerSummary(
                worker_id=worker["worker_id"],
                status=status,
                spider=spider,
            )
        )
    return summaries


@router.get(
    "/workers/{worker_id}/realtime",
    response_model=WorkerRealtimeResponse,
    summary="获取 Worker 实时数据",
)
async def get_worker_realtime(
    worker_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    await _ensure_worker_access(worker_id, current_user)
    data = await monitoring_service.get_worker_realtime(worker_id)
    if not data:
        raise HTTPException(status_code=404, detail="Worker 实时数据不存在")
    points = [WorkerRealtimePoint(**item) for item in data]
    return WorkerRealtimeResponse(worker_id=worker_id, data=points)


def _require_timezone(dt: datetime, field: str) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise HTTPException(status_code=400, detail=f"{field} 必须包含时区信息")
    return dt


@router.get(
    "/workers/{worker_id}/history",
    response_model=WorkerHistoryQueryResponse,
    summary="获取 Worker 历史数据",
)
async def get_worker_history(
    worker_id: str,
    metric_type: str = Query("performance", pattern="^(performance|spider)$"),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    current_user: TokenData = Depends(get_current_user),
):
    await _ensure_worker_access(worker_id, current_user)
    if end_time is None:
        end_time = datetime.now(UTC)
    if start_time is None:
        start_time = end_time - timedelta(hours=24)

    start_time = _require_timezone(start_time, "start_time")
    end_time = _require_timezone(end_time, "end_time")

    if start_time >= end_time:
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")

    start_time_utc = start_time.astimezone(UTC)
    end_time_utc = end_time.astimezone(UTC)

    records = await monitoring_service.get_worker_history(worker_id, start_time_utc, end_time_utc, metric_type)
    if not records:
        return WorkerHistoryQueryResponse(worker_id=worker_id, metric_type=metric_type, data=[], count=0)

    items = [_worker_history_item(record) for record in records]

    return WorkerHistoryQueryResponse(worker_id=worker_id, metric_type=metric_type, data=items, count=len(items))


def _worker_history_item(record: dict[str, Any]) -> WorkerHistoryItem:
    transformed = {key: _history_value(value) for key, value in record.items()}
    for extra_key in ("id", "worker_id", "created_at", "status"):
        transformed.pop(extra_key, None)
    return WorkerHistoryItem.model_validate(transformed)


def _history_value(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    return value


@router.get(
    "/cluster/summary",
    response_model=ClusterSummaryResponse,
    summary="获取集群摘要",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_cluster_summary(current_user: TokenData = Depends(get_current_user)):
    await _ensure_authenticated_user(current_user)
    summary = await monitoring_service.get_cluster_summary()
    return ClusterSummaryResponse(**summary)
