from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from src.schemas.monitoring import (
    ClusterSummaryResponse,
    HistoryQueryResponse,
    NodeHistoryItem,
    NodeRealtimePoint,
    NodeRealtimeResponse,
    NodeSpiderStats,
    NodeStatus,
    NodeSummary,
)
from src.services.monitoring import monitoring_service

router = APIRouter(prefix="/monitoring", tags=["监控"])


def _convert_status(raw):
    result = {}
    for key, value in raw.items():
        if value is None or value == "":
            result[key] = None
            continue
        try:
            if key in {"cpu_percent", "memory_percent", "disk_percent"}:
                result[key] = float(value)
            elif key in {"network_sent_mb", "network_recv_mb"}:
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


@router.get("/nodes", response_model=list[NodeSummary], summary="列出在线节点")
async def list_online_nodes():
    nodes = await monitoring_service.get_online_nodes()
    summaries = []
    for node in nodes:
        status = NodeStatus(**_convert_status(node.get("status", {})))
        spider = NodeSpiderStats(**_convert_spider(node.get("spider", {})))
        summaries.append(
            NodeSummary(
                node_id=node["node_id"],
                status=status,
                spider=spider,
            )
        )
    return summaries


@router.get("/nodes/{node_id}/realtime", response_model=NodeRealtimeResponse, summary="获取节点实时数据")
async def get_node_realtime(node_id):
    data = await monitoring_service.get_node_realtime(node_id)
    if not data:
        raise HTTPException(status_code=404, detail="节点实时数据不存在")
    points = [NodeRealtimePoint(**item) for item in data]
    return NodeRealtimeResponse(node_id=node_id, data=points)


def _require_timezone(dt, field):
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise HTTPException(status_code=400, detail=f"{field} 必须包含时区信息")
    return dt


@router.get("/nodes/{node_id}/history", response_model=HistoryQueryResponse, summary="获取节点历史数据")
async def get_node_history(
    node_id,
    metric_type=Query("performance", pattern="^(performance|spider)$"),
    start_time=None,
    end_time=None,
):
    if end_time is None:
        end_time = datetime.now(timezone.utc)
    if start_time is None:
        start_time = end_time - timedelta(hours=24)

    start_time = _require_timezone(start_time, "start_time")
    end_time = _require_timezone(end_time, "end_time")

    if start_time >= end_time:
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")

    start_time_utc = start_time.astimezone(timezone.utc)
    end_time_utc = end_time.astimezone(timezone.utc)

    records = await monitoring_service.get_node_history(
        node_id, start_time_utc, end_time_utc, metric_type
    )
    if not records:
        return HistoryQueryResponse(node_id=node_id, metric_type=metric_type, data=[], count=0)

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
        for extra_key in ("id", "node_id", "created_at", "status"):
            transformed.pop(extra_key, None)
        items.append(NodeHistoryItem(**transformed))

    return HistoryQueryResponse(node_id=node_id, metric_type=metric_type, data=items, count=len(items))


@router.get("/cluster/summary", response_model=ClusterSummaryResponse, summary="获取集群摘要")
async def get_cluster_summary():
    summary = await monitoring_service.get_cluster_summary()
    return ClusterSummaryResponse(**summary)

