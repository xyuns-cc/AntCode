"""
gRPC 指标 API 端点

提供 gRPC 服务器统计信息的 API 接口。

Requirements: 10.4
"""
from datetime import datetime

from fastapi import APIRouter

from src.schemas.monitoring import (
    GrpcMetricsResponse,
    GrpcServerMetrics,
    GrpcConnectionStats,
    GrpcMessageStats,
    GrpcLatencyStats,
    GrpcErrorStats,
)
from src.services.grpc.config import grpc_config
from src.services.grpc.server import get_grpc_server
from src.services.grpc.metrics import get_grpc_metrics_collector
from src.services.grpc.node_service_impl import get_node_service_impl

router = APIRouter(prefix="/grpc", tags=["gRPC 监控"])


@router.get("/metrics", response_model=GrpcMetricsResponse, summary="获取 gRPC 服务器指标")
async def get_grpc_metrics() -> GrpcMetricsResponse:
    """
    获取 gRPC 服务器的统计信息
    
    包括：
    - 连接统计：当前连接数、总连接数、活跃流数量
    - 消息统计：发送/接收的消息数和字节数
    - 延迟统计：平均、最小、最大、P95、P99 延迟
    - 错误统计：错误数、最近错误信息
    
    Requirements: 10.4
    """
    grpc_server = get_grpc_server()
    metrics_collector = get_grpc_metrics_collector()
    node_service = get_node_service_impl()
    
    # 获取连接统计
    connection_stats = GrpcConnectionStats(
        connected_nodes=node_service.connection_count,
        total_connections=metrics_collector.total_connections,
        active_streams=metrics_collector.active_connections,
    )
    
    # 获取消息统计
    message_stats = GrpcMessageStats(
        messages_sent=metrics_collector.messages_sent,
        messages_received=metrics_collector.messages_received,
        bytes_sent=metrics_collector.bytes_sent,
        bytes_received=metrics_collector.bytes_received,
    )
    
    # 获取延迟统计
    latency_stats = GrpcLatencyStats(
        avg_latency_ms=round(metrics_collector.avg_latency_ms, 2),
        min_latency_ms=round(metrics_collector.min_latency_ms, 2),
        max_latency_ms=round(metrics_collector.max_latency_ms, 2),
        p95_latency_ms=round(metrics_collector.p95_latency_ms, 2),
        p99_latency_ms=round(metrics_collector.p99_latency_ms, 2),
        sample_count=metrics_collector.latency_sample_count,
    )
    
    # 获取错误统计
    error_stats = GrpcErrorStats(
        error_count=metrics_collector.error_count,
        last_error=metrics_collector.last_error,
        last_error_time=metrics_collector.last_error_time,
        reconnect_count=metrics_collector.reconnect_count,
    )
    
    # 构建服务器指标
    server_metrics = GrpcServerMetrics(
        enabled=grpc_config.enabled,
        running=grpc_server.is_running,
        port=grpc_config.port,
        connection=connection_stats,
        messages=message_stats,
        latency=latency_stats,
        errors=error_stats,
        uptime_seconds=round(metrics_collector.uptime_seconds, 2) if metrics_collector.started_at else None,
        started_at=metrics_collector.started_at,
    )
    
    return GrpcMetricsResponse(
        server=server_metrics,
        timestamp=datetime.now(),
    )


@router.get("/status", summary="获取 gRPC 服务器状态")
async def get_grpc_status() -> dict:
    """
    获取 gRPC 服务器的简要状态
    
    返回服务器是否启用、是否运行、端口号和连接数。
    """
    grpc_server = get_grpc_server()
    node_service = get_node_service_impl()
    
    return {
        "enabled": grpc_config.enabled,
        "running": grpc_server.is_running,
        "port": grpc_config.port,
        "connected_nodes": node_service.connection_count,
        "node_ids": node_service.connected_nodes,
    }


@router.get("/connections", summary="获取 gRPC 连接列表")
async def get_grpc_connections() -> dict:
    """
    获取当前所有 gRPC 连接的详细信息
    """
    node_service = get_node_service_impl()
    
    connections = []
    for node_id in node_service.connected_nodes:
        conn = await node_service.get_connection(node_id)
        if conn:
            connections.append({
                "node_id": node_id,
                "connected_at": conn.connected_at.isoformat(),
                "is_closed": conn.is_closed,
            })
    
    return {
        "total": len(connections),
        "connections": connections,
    }
