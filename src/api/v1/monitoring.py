from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List

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


def _convert_status(raw: Dict[str, str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
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


def _convert_spider(raw: Dict[str, str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
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


@router.get("/nodes", response_model=List[NodeSummary], summary="获取在线节点列表")
async def list_online_nodes():
    nodes = await monitoring_service.get_online_nodes()
    summaries: List[NodeSummary] = []
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


@router.get(
    "/nodes/{node_id}/realtime",
    response_model=NodeRealtimeResponse,
    summary="获取节点实时监控数据",
)
async def get_node_realtime(node_id: str):
    data = await monitoring_service.get_node_realtime(node_id)
    if not data:
        raise HTTPException(status_code=404, detail="节点实时数据不存在")
    points = [NodeRealtimePoint(**item) for item in data]
    return NodeRealtimeResponse(node_id=node_id, data=points)


def _require_timezone(dt: datetime, field: str) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise HTTPException(
            status_code=400,
            detail=f"{field} 必须包含时区信息，例如 2025-11-14T10:00:00+08:00",
        )
    return dt


@router.get(
    "/nodes/{node_id}/history",
    response_model=HistoryQueryResponse,
    summary="查询节点历史监控数据",
)
async def get_node_history(
    node_id: str,
    metric_type: str = Query("performance", pattern="^(performance|spider)$"),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
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

    items: List[NodeHistoryItem] = []
    for record in records:
        transformed: Dict[str, Any] = {}
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


@router.get(
    "/cluster/summary",
    response_model=ClusterSummaryResponse,
    summary="获取集群汇总统计",
)
async def get_cluster_summary():
    summary = await monitoring_service.get_cluster_summary()
    return ClusterSummaryResponse(**summary)

