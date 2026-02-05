"""节点管理路由 - 与主控 API 风格保持一致"""

from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from loguru import logger

from ..deps import get_engine
from ..schemas import ConnectRequestV2, DisconnectRequest
from ...config import get_node_config, reset_machine_code
from ...services import master_client

router = APIRouter(prefix="/node", tags=["节点管理"])


# ============ 响应模型 ============

class EngineInfo(BaseModel):
    """引擎信息"""
    state: str
    running_tasks: int
    pending_tasks: int
    max_concurrent: int


class WebSocketInfo(BaseModel):
    """WebSocket 信息"""
    connected: bool
    stats: Optional[dict] = None


class SystemInfo(BaseModel):
    """系统信息"""
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    python_version: Optional[str] = None
    machine_arch: Optional[str] = None


class NodeInfoResponse(BaseModel):
    """节点信息响应"""
    name: str
    host: Optional[str] = None
    port: Optional[int] = None
    region: Optional[str] = None
    machine_code: str
    is_connected: bool
    master_url: Optional[str] = None
    version: Optional[str] = None
    engine: EngineInfo
    websocket: WebSocketInfo
    capabilities: Optional[dict] = None  # 节点能力
    system: Optional[SystemInfo] = None  # 系统信息
    metrics: Optional[dict] = None  # 系统指标


class ConnectResponse(BaseModel):
    """连接响应"""
    name: str
    machine_code: str
    node_id: Optional[str] = None
    connection_type: str


class MachineCodeResetResponse(BaseModel):
    """机器码重置响应"""
    old_machine_code: str
    new_machine_code: str


# ============ 路由 ============

@router.get("/info", response_model=NodeInfoResponse)
async def get_node_info():
    """获取节点信息"""
    import platform
    import sys
    from ...services import capability_service
    from ...transport.manager import communication_manager

    info = master_client.get_node_info()
    engine = get_engine()
    metrics = master_client.get_system_metrics()
    config = get_node_config()

    # 获取系统信息
    system_info = SystemInfo(
        os_type=platform.system(),
        os_version=platform.release(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        machine_arch=platform.machine(),
    )

    # 使用统一通讯管理器的状态
    comm_stats = communication_manager.get_stats()

    return NodeInfoResponse(
        name=info.get("name", "unknown"),
        host=info.get("host"),
        port=info.get("port"),
        region=info.get("region"),
        machine_code=info.get("machine_code", ""),
        is_connected=communication_manager.is_connected,
        master_url=config.master_url,
        version=info.get("version"),
        engine=EngineInfo(
            state=engine.state.name,
            running_tasks=engine.running_count,
            pending_tasks=engine.pending_count,
            max_concurrent=engine.max_concurrent,
        ),
        websocket=WebSocketInfo(
            connected=comm_stats.get("connected", False),
            stats=comm_stats,
        ),
        capabilities=capability_service.detect_all(),
        system=system_info,
        metrics=metrics,
    )


@router.get("/stats")
async def get_node_stats():
    """获取引擎统计"""
    engine = get_engine()
    return engine.get_stats()


@router.get("/communication")
async def get_communication_stats():
    """获取通讯状态统计"""
    from ...transport.manager import communication_manager
    return communication_manager.get_stats()


@router.get("/metrics")
async def get_metrics():
    """获取节点指标"""
    return master_client.get_system_metrics()


@router.post("/connect/v2", response_model=ConnectResponse)
async def connect_node_v2(request: ConnectRequestV2):
    """连接主节点（gRPC 优先，HTTP 回退）"""
    from ...transport.manager import communication_manager
    from ...domain.models import ConnectionConfig

    config = get_node_config()

    if request.machine_code != config.machine_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="机器码不匹配"
        )
    if not request.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少访问令牌"
        )

    config.is_connected = True
    config.api_key = request.api_key
    config.access_token = request.access_token
    config.master_url = request.master_url
    config.secret_key = request.secret_key
    config.node_id = request.node_id
    if request.grpc_port:
        config.grpc_port = request.grpc_port
    config.prefer_grpc = bool(request.prefer_grpc)

    # 先建立 HTTP 连接（心跳/执行心跳等）
    try:
        await master_client.connect(
            master_url=request.master_url,
            machine_code=config.machine_code,
            api_key=request.api_key,
            access_token=request.access_token,
            secret_key=request.secret_key,
            node_id=request.node_id,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"主节点连接失败: {e}")

    connection_config = ConnectionConfig(
        master_url=request.master_url,
        node_id=request.node_id,
        api_key=request.api_key,
        machine_code=config.machine_code,
        access_token=request.access_token,
        secret_key=request.secret_key,
        grpc_port=config.grpc_port,
        prefer_grpc=config.prefer_grpc,
        heartbeat_interval=config.heartbeat_interval,
        reconnect_base_delay=config.grpc_reconnect_base_delay,
        reconnect_max_delay=config.grpc_reconnect_max_delay,
    )

    try:
        connected = await communication_manager.connect(connection_config)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"通信连接失败: {e}")

    if not connected:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="通信连接失败")

    # 获取实际使用的协议
    protocol = communication_manager.current_protocol
    connection_type = protocol.value if protocol.value != "none" else "http"

    logger.info(f"已连接主节点: {request.master_url} (协议: {connection_type})")

    return ConnectResponse(
        name=config.name,
        machine_code=config.machine_code,
        node_id=request.node_id,
        connection_type=connection_type,
    )


@router.post("/disconnect")
async def disconnect_node(request: DisconnectRequest):
    """断开主节点连接"""
    from ...transport.manager import communication_manager

    config = get_node_config()

    if request.machine_code != config.machine_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="机器码不匹配"
        )

    config.is_connected = False
    config.api_key = None
    config.access_token = None
    config.master_url = None
    config.secret_key = None
    config.node_id = None

    # 使用统一通讯管理器断开连接
    await communication_manager.disconnect()
    try:
        await master_client.disconnect()
    except Exception:
        pass
    logger.info("已断开主节点连接")

    return {"disconnected": True}


@router.post("/reset-machine-code", response_model=MachineCodeResetResponse)
async def reset_machine_code_endpoint():
    """重置机器码"""
    from ...transport.manager import communication_manager
    config = get_node_config()

    old_code = config.machine_code
    new_code = reset_machine_code()
    config.machine_code = new_code
    config.is_connected = False
    config.master_url = None
    config.api_key = None
    config.access_token = None
    config.secret_key = None
    config.node_id = None

    await communication_manager.disconnect()
    await master_client.disconnect()

    return MachineCodeResetResponse(
        old_machine_code=old_code,
        new_machine_code=new_code,
    )


# ============ 资源管理 ============

@router.get("/resources")
async def get_resource_limits():
    """获取当前资源限制和监控状态"""
    from ...services.resource_monitor import resource_monitor

    config = get_node_config()
    resource_stats = resource_monitor.get_resource_stats()

    return {
        "limits": {
            "max_concurrent_tasks": config.max_concurrent_tasks,
            "task_memory_limit_mb": config.task_memory_limit_mb,
            "task_cpu_time_limit_sec": config.task_cpu_time_limit_sec,
            "task_timeout": config.task_timeout,
        },
        "auto_adjustment": config.auto_resource_limit,
        "resource_stats": resource_stats,
    }


@router.post("/resources")
async def update_resource_limits(
    max_concurrent_tasks: Optional[int] = None,
    task_memory_limit_mb: Optional[int] = None,
    task_cpu_time_limit_sec: Optional[int] = None,
    auto_resource_limit: Optional[bool] = None,
):
    """手动调整资源限制"""
    from ...services.resource_monitor import resource_monitor

    config = get_node_config()
    engine = get_engine()
    updated = {}

    if max_concurrent_tasks is not None and max_concurrent_tasks > 0:
        config.max_concurrent_tasks = max_concurrent_tasks
        updated["max_concurrent_tasks"] = max_concurrent_tasks

    if task_memory_limit_mb is not None and task_memory_limit_mb > 0:
        config.task_memory_limit_mb = task_memory_limit_mb
        updated["task_memory_limit_mb"] = task_memory_limit_mb

    if task_cpu_time_limit_sec is not None and task_cpu_time_limit_sec > 0:
        config.task_cpu_time_limit_sec = task_cpu_time_limit_sec
        updated["task_cpu_time_limit_sec"] = task_cpu_time_limit_sec

    if auto_resource_limit is not None:
        config.auto_resource_limit = auto_resource_limit
        updated["auto_resource_limit"] = auto_resource_limit

        # 启动或停止资源监控
        if auto_resource_limit:
            await resource_monitor.start_monitoring()
        else:
            await resource_monitor.stop_monitoring()

    # 通知引擎更新配置
    if engine and updated and hasattr(engine, 'update_config'):
        try:
            await engine.update_config(updated)
        except Exception as e:
            logger.error(f"更新引擎配置失败: {e}")

    logger.info(f"资源限制已更新: {updated}")

    return {
        "success": True,
        "updated": updated,
        "current_limits": {
            "max_concurrent_tasks": config.max_concurrent_tasks,
            "task_memory_limit_mb": config.task_memory_limit_mb,
            "task_cpu_time_limit_sec": config.task_cpu_time_limit_sec,
            "auto_resource_limit": config.auto_resource_limit,
        },
    }
