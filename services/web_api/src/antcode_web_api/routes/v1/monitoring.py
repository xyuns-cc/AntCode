from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

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
from antcode_core.application.services.monitoring import monitoring_service

router = APIRouter()


def _convert_status(raw):
    result = {}
    for key, value in raw.items():
        if value is None or value == "":
            result[key] = None
            continue
        try:
            if key in {"cpu_percent", "memory_percent", "disk_percent"} or key in {"network_sent_mb", "network_recv_mb"}:
                result[key] = float(value)
            elif key in {"memory_used_mb", "uptime_seconds", "update_time"}:
                result[key] = int(value)
            else:
                result[key] = value
        except (TypeError, ValueError):
            result[key] = value
    return result


def _convert_spider(raw):
    result = {}
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
async def list_online_workers():
    workers = await monitoring_service.get_online_workers()
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
async def get_worker_realtime(worker_id: str):
    data = await monitoring_service.get_worker_realtime(worker_id)
    if not data:
        raise HTTPException(status_code=404, detail="Worker 实时数据不存在")
    points = [WorkerRealtimePoint(**item) for item in data]
    return WorkerRealtimeResponse(worker_id=worker_id, data=points)


def _require_timezone(dt, field):
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
    start_time: datetime = None,
    end_time: datetime = None,
):
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

    records = await monitoring_service.get_worker_history(
        worker_id, start_time_utc, end_time_utc, metric_type
    )
    if not records:
        return WorkerHistoryQueryResponse(
            worker_id=worker_id, metric_type=metric_type, data=[], count=0
        )

    items = []
    for record in records:
        transformed = {}
        for key, value in record.items():
            if isinstance(value, datetime):
                transformed[key] = value
            elif value is None:
                transformed[key] = None
            elif isinstance(value, Decimal):
                transformed[key] = float(value)
            else:
                transformed[key] = value
        for extra_key in ("id", "worker_id", "created_at", "status"):
            transformed.pop(extra_key, None)
        items.append(WorkerHistoryItem(**transformed))

    return WorkerHistoryQueryResponse(
        worker_id=worker_id, metric_type=metric_type, data=items, count=len(items)
    )


@router.get("/cluster/summary", response_model=ClusterSummaryResponse, summary="获取集群摘要")
async def get_cluster_summary():
    summary = await monitoring_service.get_cluster_summary()
    return ClusterSummaryResponse(**summary)
