"""节点管理 API"""

from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body, Request
from loguru import logger

from src.core.security.auth import TokenData, get_current_super_admin, get_current_user, jwt_auth
from src.core.security.node_auth import verify_node_request_with_signature, verify_node_request
from src.core.response import success, BaseResponse
from src.models import NodeStatus
from src.services.nodes import node_service
from src.services.audit import audit_service
from src.models.audit_log import AuditAction
from src.schemas.node import (
    NodeCreateRequest, NodeUpdateRequest, NodeResponse, NodeListResponse,
    NodeAggregateStats, NodeTestConnectionResponse, NodeHeartbeatRequest,
    NodeRegisterRequest, NodeRegisterResponse, NodeMetrics, NodeConnectRequest,
    NodeRebindRequest
)
from src.utils.node_request import build_node_signed_headers

router = APIRouter(prefix="/nodes", tags=["节点管理"])


async def _build_service_access_token(current_user: TokenData) -> str:
    from src.services.sessions.session_service import user_session_service

    session = await user_session_service.get_or_create_service_session(current_user.user_id)
    return jwt_auth.create_access_token(
        user_id=current_user.user_id,
        username=current_user.username,
        session_id=session.public_id,
        token_type="service",
    )


def _build_node_base_url(node):
    host = (getattr(node, "host", "") or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="节点地址无效")

    scheme = "http"
    hostname = host
    port = getattr(node, "port", None)

    if host.startswith(("http://", "https://")):
        parsed = urlparse(host)
        scheme = parsed.scheme
        hostname = parsed.hostname
        port = parsed.port or port
        if parsed.path not in ("", "/"):
            raise HTTPException(status_code=400, detail="节点地址包含非法路径")

    if scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="不支持的节点协议")
    if not hostname or not port:
        raise HTTPException(status_code=400, detail="节点地址无效")

    return f"{scheme}://{hostname}:{port}"


def _node_to_response(node) -> NodeResponse:
    """将节点模型转换为响应对象"""
    from src.schemas.node import NodeCapabilities

    metrics = None
    if node.metrics:
        metrics = NodeMetrics(**node.metrics)

    # 解析节点能力
    capabilities = None
    has_render = False
    if node.capabilities:
        try:
            capabilities = NodeCapabilities(**node.capabilities)
            has_render = capabilities.has_render_capability()
        except Exception:
            capabilities = None

    return NodeResponse(
        id=node.public_id,
        name=node.name,
        host=node.host,
        port=node.port,
        status=node.status,
        region=node.region,
        description=node.description,
        tags=node.tags,
        version=node.version,
        # 操作系统信息
        osType=getattr(node, 'os_type', None),
        osVersion=getattr(node, 'os_version', None),
        pythonVersion=getattr(node, 'python_version', None),
        machineArch=getattr(node, 'machine_arch', None),
        # 节点能力
        capabilities=capabilities,
        hasRenderCapability=has_render,
        metrics=metrics,
        lastHeartbeat=node.last_heartbeat,
        createdAt=node.created_at,
        updatedAt=node.updated_at
    )


@router.get(
    "",
    response_model=BaseResponse[NodeListResponse],
    summary="获取节点列表",
    description="获取所有节点列表，支持分页和过滤"
)
async def get_nodes(
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    status_filter: Optional[str] = Query(None, alias="status", description="状态过滤"),
    region: Optional[str] = Query(None, description="区域过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点列表"""
    nodes, total = await node_service.get_nodes(
        page=page,
        size=size,
        status_filter=status_filter,
        region=region,
        search=search
    )

    items = [_node_to_response(node) for node in nodes]

    return success(
        NodeListResponse(
            items=items,
            total=total,
            page=page,
            size=size
        )
    )


@router.get(
    "/stats",
    response_model=BaseResponse[NodeAggregateStats],
    summary="获取节点统计",
    description="获取所有节点的聚合统计信息"
)
async def get_node_stats(
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点统计信息"""
    stats = await node_service.get_aggregate_stats()
    return success(stats)


@router.get(
    "/cluster/metrics/history",
    response_model=BaseResponse[dict],
    summary="获取集群历史指标",
    description="获取所有节点的聚合历史指标"
)
async def get_cluster_metrics_history(
    hours: int = Query(24, ge=1, le=720, description="查询时间范围（小时）"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取集群历史指标"""
    history = await node_service.get_cluster_metrics_history(hours=hours)
    return success(history)


@router.post(
    "",
    response_model=BaseResponse[NodeResponse],
    summary="创建节点",
    description="手动创建新的工作节点（不推荐，建议使用连接方式）"
)
async def create_node(
    request: NodeCreateRequest,
    http_request: Request,
    current_user: TokenData = Depends(get_current_user)
):
    """创建节点"""
    from src.services.users.user_service import user_service
    node = await node_service.create_node(request, current_user.user_id)

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log(
        action=AuditAction.NODE_CREATE,
        resource_type="node",
        username=user.username if user else "unknown",
        resource_id=node.public_id,
        resource_name=node.name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"创建节点: {node.name}"
    )

    return success(_node_to_response(node), message="节点创建成功")


@router.post(
    "/connect",
    response_model=BaseResponse[NodeResponse],
    summary="连接节点",
    description="通过地址和机器码连接工作节点"
)
async def connect_node(
    request: NodeConnectRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """连接节点（推荐方式）"""
    from src.core.config import settings
    master_url = settings.master_url

    node = await node_service.connect_node(
        request, 
        master_url=master_url,
        user_id=current_user.user_id
    )
    return success(_node_to_response(node), message="节点连接成功")


@router.post(
    "/{node_id}/disconnect",
    response_model=BaseResponse[dict],
    summary="断开节点",
    description="断开与节点的连接"
)
async def disconnect_node(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """断开节点连接"""
    result = await node_service.disconnect_node(node_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )
    return success({"disconnected": True}, message="节点已断开")


@router.get(
    "/{node_id}",
    response_model=BaseResponse[NodeResponse],
    summary="获取节点详情",
    description="根据ID获取节点详细信息"
)
async def get_node(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点详情"""
    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )
    return success(_node_to_response(node))


@router.put(
    "/{node_id}",
    response_model=BaseResponse[NodeResponse],
    summary="更新节点",
    description="更新节点信息"
)
async def update_node(
    node_id: str,
    request: NodeUpdateRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """更新节点"""
    node = await node_service.update_node(node_id, request)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )
    return success(_node_to_response(node), message="节点更新成功")


@router.post(
    "/{node_id}/rebind",
    response_model=BaseResponse[NodeResponse],
    summary="重新绑定节点机器码",
    description="当节点重启后机器码变化时，使用此接口更新机器码而无需删除重建节点"
)
async def rebind_node(
    node_id: str,
    request: NodeRebindRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """
    重新绑定节点机器码
    
    使用场景:
    - 节点重启后机器码发生变化
    - 节点迁移到新硬件
    - 节点重置了机器码
    
    参数:
    - new_machine_code: 新的机器码（从节点启动日志中获取）
    - verify_connection: 是否验证新机器码与节点匹配（推荐开启）
    """
    from src.models import User

    # 检查是否是管理员
    user = await User.filter(id=current_user.user_id).first()
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有管理员可以重新绑定节点"
        )

    result = await node_service.rebind_node(
        node_id=node_id,
        new_machine_code=request.new_machine_code,
        verify_connection=request.verify_connection
    )

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "重新绑定失败")
        )

    node = result["node"]
    return success(_node_to_response(node), message="节点机器码已更新")


@router.delete(
    "/{node_id}",
    response_model=BaseResponse[dict],
    summary="删除节点",
    description="删除指定节点"
)
async def delete_node(
    node_id: str,
    http_request: Request,
    current_user: TokenData = Depends(get_current_user)
):
    """删除节点"""
    from src.services.users.user_service import user_service

    # 获取节点信息用于审计
    node = await node_service.get_node_by_public_id(node_id)
    node_name = node.name if node else node_id

    deleted = await node_service.delete_node(node_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log(
        action=AuditAction.NODE_DELETE,
        resource_type="node",
        username=user.username if user else "unknown",
        resource_id=node_id,
        resource_name=node_name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"删除节点: {node_name}"
    )

    return success({"deleted": True}, message="节点删除成功")


@router.post(
    "/batch-delete",
    response_model=BaseResponse[dict],
    summary="批量删除节点",
    description="批量删除多个节点"
)
async def batch_delete_nodes(
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """批量删除节点"""
    node_ids = request.get("node_ids", [])
    if not node_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="节点ID列表不能为空"
        )

    result = await node_service.batch_delete_nodes(node_ids)

    if result["failed_count"] == 0:
        message = f"成功删除 {result['success_count']} 个节点"
    elif result["success_count"] == 0:
        message = f"删除失败，{result['failed_count']} 个节点删除失败"
    else:
        message = f"部分成功：{result['success_count']} 个删除成功，{result['failed_count']} 个失败"

    return success(result, message=message)


@router.post(
    "/{node_id}/test",
    response_model=BaseResponse[NodeTestConnectionResponse],
    summary="测试节点连接",
    description="测试与节点的网络连接"
)
async def test_node_connection(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """测试节点连接"""
    result = await node_service.test_connection(node_id)
    return success(NodeTestConnectionResponse(**result))


@router.post(
    "/{node_id}/refresh",
    response_model=BaseResponse[NodeResponse],
    summary="刷新节点状态",
    description="重新检测并更新节点状态"
)
async def refresh_node_status(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """刷新节点状态"""
    node = await node_service.refresh_node_status(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )
    return success(_node_to_response(node))


# ====== 节点权限管理 API（需要管理员权限）======

@router.get(
    "/my/available",
    response_model=BaseResponse[NodeListResponse],
    summary="获取我可用的节点",
    description="获取当前用户有权限访问的节点列表"
)
async def get_my_available_nodes(
    current_user: TokenData = Depends(get_current_user)
):
    """获取当前用户可用的节点列表"""
    from src.models import User

    # 从数据库获取用户信息
    user = await User.get_or_none(id=current_user.user_id)
    is_admin = user.is_admin if user else False

    nodes = await node_service.get_user_nodes(
        user_id=current_user.user_id,
        is_admin=is_admin
    )

    items = [_node_to_response(node) for node in nodes]

    return success(
        NodeListResponse(
            items=items,
            total=len(items),
            page=1,
            size=len(items)
        )
    )


@router.get(
    "/{node_id}/users",
    response_model=BaseResponse[list],
    summary="获取节点授权用户",
    description="获取该节点的授权用户列表（管理员）"
)
async def get_node_users(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点的授权用户列表"""
    from src.models import User

    # 从数据库获取用户信息以检查管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    users = await node_service.get_node_users(node.id)
    return success(users)


@router.post(
    "/{node_id}/assign",
    response_model=BaseResponse[dict],
    summary="分配节点权限",
    description="给用户分配节点访问权限（管理员）"
)
async def assign_node_permission(
    node_id: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """分配节点权限给用户"""
    from src.models import User

    # 从数据库获取用户信息以检查管理员权限
    admin_user = await User.get_or_none(id=current_user.user_id)
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    user_id = request.get("user_id")
    permission = request.get("permission", "use")
    note = request.get("note")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID不能为空"
        )

    user = await User.filter(public_id=str(user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user_id = user.id

    await node_service.assign_node_to_user(
        node_id=node.id,
        user_id=user_id,
        permission=permission,
        assigned_by=current_user.user_id,
        note=note
    )

    return success({"assigned": True}, message="权限分配成功")


@router.delete(
    "/{node_id}/revoke/{user_id}",
    response_model=BaseResponse[dict],
    summary="撤销节点权限",
    description="撤销用户的节点访问权限（管理员）"
)
async def revoke_node_permission(
    node_id: str,
    user_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """撤销用户的节点权限"""
    from src.models import User

    # 从数据库获取用户信息以检查管理员权限
    admin_user = await User.get_or_none(id=current_user.user_id)
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    user = await User.filter(public_id=str(user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    internal_user_id = user.id

    revoked = await node_service.revoke_node_from_user(node.id, internal_user_id)

    if revoked:
        return success({"revoked": True}, message="权限撤销成功")
    else:
        return success({"revoked": False}, message="该用户没有此节点权限")


@router.post(
    "/batch-assign",
    response_model=BaseResponse[dict],
    summary="批量分配节点权限",
    description="批量给用户分配多个节点权限（管理员）"
)
async def batch_assign_nodes(
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """批量分配节点权限"""
    from src.models import User

    # 从数据库获取用户信息以检查管理员权限
    admin_user = await User.get_or_none(id=current_user.user_id)
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    user_id = request.get("user_id")
    node_ids = request.get("node_ids", [])
    permission = request.get("permission", "use")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID不能为空"
        )

    if not node_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="节点ID列表不能为空"
        )

    # 获取用户的内部ID
    user = await User.filter(public_id=str(user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user_id = user.id

    # 获取节点的内部ID
    internal_ids = []
    for nid in node_ids:
        node = await node_service.get_node_by_public_id(str(nid))
        if node:
            internal_ids.append(node.id)

    result = await node_service.batch_assign_nodes(
        user_id=user_id,
        node_ids=internal_ids,
        permission=permission,
        assigned_by=current_user.user_id
    )

    return success(result, message=f"成功分配 {result['success']} 个节点权限")


# ====== 节点端调用的 API（需要用户认证）======

@router.get(
    "/{node_id}/metrics/history",
    response_model=BaseResponse[list],
    summary="获取节点历史指标",
    description="获取节点的历史指标数据用于图表展示"
)
async def get_node_metrics_history(
    node_id: str,
    hours: int = Query(24, ge=1, le=720, description="查询时间范围（小时）"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点历史指标"""
    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    history = await node_service.get_metrics_history(node.id, hours=hours)
    return success(history)


@router.post(
    "/register",
    response_model=BaseResponse[NodeRegisterResponse],
    summary="节点注册",
    description="工作节点主动注册到主节点"
)
async def register_node(
    request: NodeRegisterRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """节点注册（节点主动调用）"""
    node, api_key, secret_key = await node_service.register_node(request)
    return success(
        NodeRegisterResponse(
            id=node.public_id,
            api_key=api_key,
            secret_key=secret_key
        ),
        message="节点注册成功"
    )


@router.post(
    "/heartbeat",
    response_model=BaseResponse[dict],
    summary="节点心跳",
    description="工作节点定期上报心跳"
)
async def node_heartbeat(
    request: NodeHeartbeatRequest,
    auth_info: dict = Depends(verify_node_request_with_signature),
    current_user: TokenData = Depends(get_current_user)
):
    """节点心跳上报（HMAC签名验证）"""
    # 处理能力上报
    capabilities_dict = None
    if request.capabilities:
        capabilities_dict = request.capabilities.model_dump()

    heartbeat_success = await node_service.heartbeat(
        node_id=request.node_id,
        api_key=request.api_key,
        status_value=request.status,
        metrics=request.metrics,
        version=request.version,
        # 操作系统信息
        os_type=request.os_type,
        os_version=request.os_version,
        python_version=request.python_version,
        machine_arch=request.machine_arch,
        # 节点能力
        capabilities=capabilities_dict,
    )

    if not heartbeat_success:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="心跳验证失败"
        )

    return success({"success": True}, message="心跳成功")


# ====== 分布式任务分发 API ======

@router.get(
    "/load/ranking",
    response_model=BaseResponse[list],
    summary="获取节点负载排名",
    description="获取所有在线节点的负载排名，用于任务分发决策"
)
async def get_nodes_load_ranking(
    region: Optional[str] = Query(None, description="区域过滤"),
    top_n: int = Query(10, ge=1, le=50, description="返回前N个节点"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点负载排名"""
    from src.services.nodes import node_load_balancer

    rankings = await node_load_balancer.get_nodes_ranking(
        region=region,
        top_n=top_n
    )
    return success(rankings)


@router.post(
    "/dispatch/task",
    response_model=BaseResponse[dict],
    summary="分发任务到节点",
    description="自动选择最佳节点或指定节点执行任务"
)
async def dispatch_task_to_node(
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """
    分发任务到节点执行（支持优先级调度）
    
    参数:
    - project_id: 项目ID (必须)
    - params: 执行参数 (可选)
    - environment_vars: 环境变量 (可选)
    - timeout: 超时时间，秒 (默认3600)
    - node_id: 指定节点ID (可选，为空则自动选择)
    - region: 指定区域 (可选)
    - tags: 指定标签 (可选)
    - priority: 优先级 0-4 (可选，0=CRITICAL, 1=HIGH, 2=NORMAL, 3=LOW, 4=IDLE)
    - project_type: 项目类型 (可选，code/file/rule，影响默认优先级)
    - require_render: 是否需要渲染能力 (可选，默认false，用于需要浏览器渲染的爬虫任务)
    """
    from src.services.nodes import node_task_dispatcher
    import uuid

    project_id = request.get("project_id")
    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="项目ID不能为空"
        )

    execution_id = str(uuid.uuid4())

    result = await node_task_dispatcher.dispatch_task(
        project_id=project_id,
        execution_id=execution_id,
        access_token=await _build_service_access_token(current_user),
        params=request.get("params"),
        environment_vars=request.get("environment_vars"),
        timeout=request.get("timeout", 3600),
        node_id=request.get("node_id"),
        region=request.get("region"),
        tags=request.get("tags"),
        priority=request.get("priority"),
        project_type=request.get("project_type", "code"),
        require_render=request.get("require_render", False),
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "任务分发失败")
        )

    return success(result, message="任务已分发到节点")


@router.post(
    "/dispatch/batch",
    response_model=BaseResponse[dict],
    summary="批量分发任务到节点",
    description="批量分发多个任务到指定节点的优先级队列"
)
async def dispatch_batch_to_node(
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """
    批量分发任务到节点执行
    
    参数:
    - tasks: 任务列表 (必须)，每个任务包含:
        - task_id: 任务ID
        - project_id: 项目ID
        - project_type: 项目类型 (code/file/rule)
        - priority: 优先级 0-4 (可选)
        - params: 执行参数 (可选)
        - environment: 环境变量 (可选)
        - timeout: 超时时间 (可选)
        - require_render: 是否需要渲染能力 (可选)
    - node_id: 指定节点ID (可选，为空则自动选择)
    - region: 指定区域 (可选)
    - tags: 指定标签 (可选)
    - batch_id: 批次ID (可选)
    - require_render: 是否需要渲染能力 (可选，默认false)
    """
    from src.services.nodes import node_task_dispatcher

    tasks = request.get("tasks")
    if not tasks or not isinstance(tasks, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务列表不能为空"
        )

    result = await node_task_dispatcher.dispatch_batch(
        tasks=tasks,
        access_token=await _build_service_access_token(current_user),
        node_id=request.get("node_id"),
        region=request.get("region"),
        tags=request.get("tags"),
        batch_id=request.get("batch_id"),
        require_render=request.get("require_render", False),
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "批量任务分发失败")
        )

    return success(result, message="批量任务已分发到节点")


@router.get(
    "/dispatch/queue/{node_id}/status",
    response_model=BaseResponse[dict],
    summary="获取节点队列状态",
    description="获取指定节点的优先级队列状态"
)
async def get_node_queue_status(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点队列状态"""
    from src.services.nodes import node_task_dispatcher

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    status_data = await node_task_dispatcher.get_queue_status(
        node,
        access_token=await _build_service_access_token(current_user),
    )
    return success(status_data)


@router.put(
    "/dispatch/queue/{node_id}/tasks/{task_id}/priority",
    response_model=BaseResponse[dict],
    summary="更新任务优先级",
    description="更新节点队列中任务的优先级"
)
async def update_node_task_priority(
    node_id: str,
    task_id: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """更新节点队列中任务的优先级"""
    from src.services.nodes import node_task_dispatcher

    priority = request.get("priority")
    if priority is None or not (0 <= priority <= 4):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="优先级必须是 0-4 之间的整数"
        )

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    result = await node_task_dispatcher.update_task_priority(
        node,
        task_id,
        priority,
        access_token=await _build_service_access_token(current_user),
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if "不存在" in result.get("error", "") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "更新优先级失败")
        )

    return success(result, message="优先级已更新")


@router.delete(
    "/dispatch/queue/{node_id}/tasks/{task_id}",
    response_model=BaseResponse[dict],
    summary="取消队列中的任务",
    description="取消节点优先级队列中的任务"
)
async def cancel_node_queued_task(
    node_id: str,
    task_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """取消节点队列中的任务"""
    from src.services.nodes import node_task_dispatcher

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    success_flag = await node_task_dispatcher.cancel_queued_task(
        node,
        task_id,
        access_token=await _build_service_access_token(current_user),
    )

    if not success_flag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在或已执行"
        )

    return success({"task_id": task_id}, message="任务已取消")


@router.get(
    "/dispatch/task/{node_id}/{task_id}/status",
    response_model=BaseResponse[dict],
    summary="获取分布式任务状态",
    description="从指定节点获取任务执行状态"
)
async def get_distributed_task_status(
    node_id: str,
    task_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """从节点获取任务状态"""
    from src.services.nodes import node_task_dispatcher

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    status_data = await node_task_dispatcher.get_task_status_from_node(
        node,
        task_id,
        access_token=await _build_service_access_token(current_user),
    )

    if not status_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在或无法获取状态"
        )

    return success(status_data)


@router.get(
    "/dispatch/task/{node_id}/{task_id}/logs",
    response_model=BaseResponse[dict],
    summary="获取分布式任务日志",
    description="从指定节点获取任务执行日志"
)
async def get_distributed_task_logs(
    node_id: str,
    task_id: str,
    log_type: str = Query("output", description="日志类型: output/error"),
    tail: int = Query(100, ge=1, le=1000, description="返回最后N行"),
    current_user: TokenData = Depends(get_current_user)
):
    """从节点获取任务日志"""
    from src.services.nodes import node_task_dispatcher

    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="节点不存在"
        )

    logs = await node_task_dispatcher.get_task_logs_from_node(
        node,
        task_id,
        access_token=await _build_service_access_token(current_user),
        log_type=log_type,
        tail=tail,
    )

    return success({
        "logs": logs,
        "total": len(logs),
        "node_id": node_id,
        "task_id": task_id
    })


@router.get(
    "/best",
    response_model=BaseResponse[dict],
    summary="获取最佳节点",
    description="根据负载自动选择最适合执行任务的节点"
)
async def get_best_node(
    region: Optional[str] = Query(None, description="区域过滤"),
    tags: Optional[str] = Query(None, description="标签过滤，逗号分隔"),
    require_render: bool = Query(False, description="是否需要渲染能力"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取当前负载最低的最佳节点"""
    from src.services.nodes import node_load_balancer

    tag_list = tags.split(",") if tags else None

    best_node = await node_load_balancer.select_best_node(
        region=region,
        tags=tag_list,
        require_render=require_render
    )

    if not best_node:
        return success({
            "available": False,
            "message": "没有可用的渲染节点" if require_render else "没有可用的节点"
        })

    score = node_load_balancer.calculate_load_score(best_node)

    return success({
        "available": True,
        "node": _node_to_response(best_node).model_dump(),
        "load_score": score
    })


@router.get(
    "/render-capable",
    response_model=BaseResponse[NodeListResponse],
    summary="获取有渲染能力的节点",
    description="获取所有具有浏览器渲染能力（DrissionPage）的在线节点"
)
async def get_render_capable_nodes(
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    region: Optional[str] = Query(None, description="区域过滤"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取有渲染能力的节点列表"""
    from src.models import Node, NodeStatus

    query = Node.filter(status=NodeStatus.ONLINE)
    if region:
        query = query.filter(region=region)

    all_nodes = await query.all()

    # 过滤有渲染能力的节点
    render_nodes = []
    for node in all_nodes:
        if node.capabilities:
            cap = node.capabilities.get("drissionpage")
            if cap and cap.get("enabled"):
                render_nodes.append(node)

    total = len(render_nodes)
    offset = (page - 1) * size
    paged_nodes = render_nodes[offset:offset + size]

    items = [_node_to_response(node) for node in paged_nodes]

    return success(
        NodeListResponse(
            items=items,
            total=total,
            page=page,
            size=size
        )
    )


# ====== 工作节点上报接口（节点调用，需要用户认证）======

@router.post(
    "/report-log",
    response_model=BaseResponse[dict],
    summary="上报任务日志",
    description="工作节点实时上报任务执行日志"
)
async def report_task_log(
    request: dict = Body(...),
    auth_info: dict = Depends(verify_node_request),
    current_user: TokenData = Depends(get_current_user)
):
    """任务日志上报（Bearer Token验证，无需签名）"""
    from src.services.nodes.distributed_log_service import distributed_log_service

    execution_id = request.get("execution_id")
    log_type = request.get("log_type", "stdout")
    content = request.get("content", "")
    machine_code = request.get("machine_code")

    if not execution_id or not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="execution_id 和 content 不能为空"
        )

    # 存储日志
    await distributed_log_service.append_log(
        execution_id=execution_id,
        log_type=log_type,
        content=content,
        machine_code=machine_code
    )

    return success({"received": True})


@router.post(
    "/report-logs-batch",
    response_model=BaseResponse[dict],
    summary="批量上报任务日志",
    description="工作节点批量上报任务执行日志"
)
async def report_task_logs_batch(
    request: dict = Body(...),
    auth_info: dict = Depends(verify_node_request),
    current_user: TokenData = Depends(get_current_user)
):
    """批量任务日志上报（Bearer Token验证，无需签名）"""
    from src.services.nodes.distributed_log_service import distributed_log_service

    logs = request.get("logs", [])
    machine_code = request.get("machine_code")

    if not logs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="logs 不能为空"
        )

    # 批量存储日志
    received_count = 0
    for log in logs:
        execution_id = log.get("execution_id")
        log_type = log.get("log_type", "stdout")
        content = log.get("content", "")

        if execution_id and content:
            await distributed_log_service.append_log(
                execution_id=execution_id,
                log_type=log_type,
                content=content,
                machine_code=machine_code
            )
            received_count += 1

    return success({"received": received_count, "total": len(logs)})


@router.post(
    "/report-heartbeat",
    response_model=BaseResponse[dict],
    summary="上报任务执行心跳",
    description="工作节点上报任务执行心跳，用于检测任务中断"
)
async def report_execution_heartbeat(
    request: dict = Body(...),
    auth_info: dict = Depends(verify_node_request),
    current_user: TokenData = Depends(get_current_user)
):
    """任务执行心跳上报"""
    from src.services.scheduler.task_persistence import task_persistence_service

    execution_id = request.get("execution_id")
    if not execution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="execution_id 不能为空"
        )

    success_flag = await task_persistence_service.update_heartbeat(execution_id)
    return success({"updated": success_flag})


@router.post(
    "/report-task",
    response_model=BaseResponse[dict],
    summary="上报任务状态",
    description="工作节点上报任务执行状态"
)
async def report_task_status(
    request: dict = Body(...),
    auth_info: dict = Depends(verify_node_request),
    current_user: TokenData = Depends(get_current_user)
):
    """任务状态上报（Bearer Token验证，无需签名）"""
    from src.services.nodes.distributed_log_service import distributed_log_service

    execution_id = request.get("execution_id")
    task_status = request.get("status")
    exit_code = request.get("exit_code")
    error_message = request.get("error_message")
    machine_code = request.get("machine_code")

    if not execution_id or not task_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="execution_id 和 status 不能为空"
        )

    # 更新任务状态
    await distributed_log_service.update_task_status(
        execution_id=execution_id,
        status=task_status,
        exit_code=exit_code,
        error_message=error_message,
        machine_code=machine_code
    )

    return success({"updated": True})


@router.get(
    "/distributed-logs/{execution_id}",
    response_model=BaseResponse[dict],
    summary="获取分布式任务日志",
    description="获取在远程节点执行的任务日志"
)
async def get_distributed_logs(
    execution_id: str,
    log_type: str = Query("stdout", description="日志类型: stdout/stderr"),
    tail: int = Query(100, ge=1, le=5000, description="返回最后N行"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取分布式任务的日志"""
    from src.services.nodes.distributed_log_service import distributed_log_service

    logs = await distributed_log_service.get_logs(
        execution_id=execution_id,
        log_type=log_type,
        tail=tail
    )

    return success({
        "execution_id": execution_id,
        "log_type": log_type,
        "logs": logs,
        "total": len(logs)
    })


# ====== 节点环境管理代理接口 ======

async def _proxy_node_request(
    node_id: str,
    method: str,
    path: str,
    json_data: dict = None,
    params: dict = None,
    timeout: float = 30.0,
    current_user: TokenData | None = None,
    require_permission: str = "use",
) -> dict:
    """代理请求到工作节点，统一做节点权限与状态校验"""
    node = await node_service.get_node_by_public_id(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")

    # 管理员或具备 use/view 权限的用户才可访问
    is_admin = False
    if current_user:
        from src.services.users.user_service import user_service
        user_obj = await user_service.get_user_by_id(current_user.user_id)
        is_admin = bool(user_obj and user_obj.is_admin)
        if not is_admin:
            allowed = await node_service.check_user_node_permission(
                user_id=current_user.user_id,
                node_id=node.id,
                is_admin=False,
                required_permission=require_permission,
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="无节点访问权限")

    if node.status != NodeStatus.ONLINE:
        raise HTTPException(status_code=400, detail=f"节点 {node.name} 当前不在线")

    base_url = _build_node_base_url(node)
    url = f"{base_url}{path}"
    payload_for_sign = json_data if json_data is not None else {}
    try:
        headers = build_node_signed_headers(node, payload_for_sign)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if method.upper() == "GET":
                response = await client.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(url, json=json_data, params=params, headers=headers)
            elif method.upper() == "DELETE":
                # DELETE 请求如果有 JSON body，需要使用 request() 方法
                if json_data:
                    response = await client.request(
                        "DELETE",
                        url,
                        params=params,
                        json=json_data,
                        headers=headers
                    )
                else:
                    response = await client.delete(url, params=params, headers=headers)
            elif method.upper() == "PUT":
                response = await client.put(url, json=json_data, headers=headers)
            elif method.upper() == "PATCH":
                response = await client.patch(url, json=json_data, headers=headers)
            else:
                raise HTTPException(status_code=400, detail=f"不支持的请求方法: {method}")

            if response.status_code >= 400:
                error_detail = response.json().get("detail", response.text) if response.text else "请求失败"
                raise HTTPException(status_code=response.status_code, detail=error_detail)

            return response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"节点 {node.name} 请求超时")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail=f"无法连接到节点 {node.name}")


@router.get(
    "/{node_id}/envs",
    response_model=BaseResponse[dict],
    summary="获取节点虚拟环境列表",
    description="获取指定节点上的所有虚拟环境"
)
async def list_node_envs(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点虚拟环境列表"""
    result = await _proxy_node_request(node_id, "GET", "/envs", current_user=current_user)
    return success(result.get("data", result))


@router.post(
    "/{node_id}/envs",
    response_model=BaseResponse[dict],
    summary="在节点上创建虚拟环境",
    description="在指定节点上创建新的虚拟环境"
)
async def create_node_env(
    node_id: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """在节点上创建虚拟环境"""
    result = await _proxy_node_request(
        node_id,
        "POST",
        "/envs",
        json_data=request,
        timeout=600,
        current_user=current_user,
    )
    return success(result.get("data", result))

# ========== 项目环境管理（优先级路由，必须在通用路由之前）==========

@router.get(
    "/{node_id}/envs/available",
    response_model=BaseResponse[dict],
    summary="获取节点可用环境列表",
    description="获取指定节点上所有可用的虚拟环境列表，支持按作用域过滤"
)
async def get_node_available_envs(
    node_id: str,
    scope: str = None,
    current_user: TokenData = Depends(get_current_user)
):
    """
    获取节点可用环境列表
    
    Args:
        node_id: 节点ID
        scope: 环境作用域过滤（可选）: public / private / all
    """
    params = {"scope": scope} if scope else {}
    result = await _proxy_node_request(
        node_id,
        "GET",
        "/envs/available",
        params=params,
        current_user=current_user,
        require_permission="view",
    )
    return success(result.get("data", result))


@router.post(
    "/{node_id}/envs/create-for-project",
    response_model=BaseResponse[dict],
    summary="为项目创建节点环境",
    description="在指定节点上为项目创建一个新的虚拟环境"
)
async def create_node_env_for_project(
    node_id: str,
    request: dict,
    current_user: TokenData = Depends(get_current_user)
):
    """
    为项目创建节点环境
    
    Request:
        {
            "name": "my-project-env",  # 可选
            "scope": "private",  # private / public
            "python_version": "3.12.0",
            "description": "项目环境",
            "packages": ["requests"]  # 可选
        }
    """
    result = await _proxy_node_request(
        node_id,
        "POST",
        "/envs/create-for-project",
        json_data=request,
        timeout=1800,
        current_user=current_user,
    )
    return success(result)


# ========== 通用环境管理 ==========

@router.get(
    "/{node_id}/envs/{env_name}",
    response_model=BaseResponse[dict],
    summary="获取节点虚拟环境详情",
    description="获取指定节点上特定虚拟环境的详细信息"
)
async def get_node_env(
    node_id: str,
    env_name: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点虚拟环境详情"""
    result = await _proxy_node_request(
        node_id,
        "GET",
        f"/envs/{env_name}",
        current_user=current_user,
        require_permission="view",
    )
    return success(result.get("data", result))


@router.delete(
    "/{node_id}/envs/{env_name}",
    response_model=BaseResponse[dict],
    summary="删除节点虚拟环境",
    description="删除指定节点上的虚拟环境"
)
async def delete_node_env(
    node_id: str,
    env_name: str,
    current_user: TokenData = Depends(get_current_user)
):
    """删除节点虚拟环境"""
    result = await _proxy_node_request(
        node_id,
        "DELETE",
        f"/envs/{env_name}",
        current_user=current_user,
    )
    return success(result.get("data", result))


@router.get(
    "/{node_id}/envs/{env_name}/packages",
    response_model=BaseResponse[dict],
    summary="获取节点虚拟环境包列表",
    description="获取指定节点虚拟环境中已安装的包"
)
async def list_node_env_packages(
    node_id: str,
    env_name: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点虚拟环境包列表"""
    result = await _proxy_node_request(
        node_id,
        "GET",
        f"/envs/{env_name}/packages",
        current_user=current_user,
        require_permission="view",
    )
    return success(result.get("data", result))


@router.post(
    "/{node_id}/envs/{env_name}/packages",
    response_model=BaseResponse[dict],
    summary="安装包到节点虚拟环境",
    description="在指定节点的虚拟环境中安装包"
)
async def install_node_env_packages(
    node_id: str,
    env_name: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """安装包到节点虚拟环境"""
    result = await _proxy_node_request(
        node_id,
        "POST",
        f"/envs/{env_name}/packages",
        json_data=request,
        timeout=1800,
        current_user=current_user,
    )
    return success(result.get("data", result))


@router.delete(
    "/{node_id}/envs/{env_name}/packages",
    response_model=BaseResponse[dict],
    summary="从节点虚拟环境卸载包",
    description="从指定节点的虚拟环境中卸载包"
)
async def uninstall_node_env_packages(
    node_id: str,
    env_name: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """从节点虚拟环境卸载包"""
    result = await _proxy_node_request(
        node_id,
        "DELETE",
        f"/envs/{env_name}/packages",
        json_data=request,
        current_user=current_user,
    )
    return success(result.get("data", result))


@router.patch(
    "/{node_id}/envs/{env_name}",
    response_model=BaseResponse[dict],
    summary="编辑节点虚拟环境",
    description="编辑指定节点上的虚拟环境信息（如 key 等）"
)
async def update_node_env(
    node_id: str,
    env_name: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user)
):
    """编辑节点虚拟环境"""
    result = await _proxy_node_request(
        node_id,
        "PATCH",
        f"/envs/{env_name}",
        json_data=request,
        current_user=current_user,
    )
    return success(result.get("data", result))


@router.get(
    "/{node_id}/interpreters",
    response_model=BaseResponse[dict],
    summary="获取节点解释器列表",
    description="获取指定节点上所有可用的Python解释器"
)
async def list_node_interpreters(
    node_id: str,
    source: Optional[str] = Query(None, description="来源过滤: local, mise, pyenv"),
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点解释器列表"""
    params = {"source": source} if source else {}
    result = await _proxy_node_request(
        node_id,
        "GET",
        "/envs/python/interpreters",
        params=params,
        current_user=current_user,
        require_permission="view",
    )
    # Worker 直接返回列表，需要包装成前端期望的格式
    interpreters = result if isinstance(result, list) else result.get("data", result) or []
    return success({
        "interpreters": interpreters,
        "total": len(interpreters)
    })


@router.post(
    "/{node_id}/interpreters/local",
    response_model=BaseResponse[dict],
    summary="在节点上注册本地解释器",
    description="在指定节点上注册本地Python解释器",
    status_code=status.HTTP_201_CREATED
)
async def register_node_interpreter(
    node_id: str,
    python_bin: str = Body(..., embed=True, description="Python 解释器路径"),
    current_user: TokenData = Depends(get_current_user)
):
    """在节点上注册本地解释器"""
    result = await _proxy_node_request(
        node_id,
        "POST",
        "/envs/python/interpreters/local",
        json_data={"python_bin": python_bin},
        current_user=current_user,
    )
    return success(result)


@router.delete(
    "/{node_id}/interpreters",
    response_model=BaseResponse[dict],
    summary="清除节点所有解释器数据",
    description="清除指定节点上所有本地注册的Python解释器数据"
)
async def clear_node_interpreters(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """清除节点所有解释器数据"""
    result = await _proxy_node_request(
        node_id,
        "DELETE",
        "/envs/python/interpreters",
        current_user=current_user,
    )
    return success(result, message="解释器数据已清除")


@router.delete(
    "/{node_id}/interpreters/{version}",
    response_model=BaseResponse[dict],
    summary="在节点上取消注册解释器",
    description="在指定节点上取消注册Python解释器"
)
async def unregister_node_interpreter(
    node_id: str,
    version: str,
    source: str = Query("local", description="来源: local, mise"),
    current_user: TokenData = Depends(get_current_user)
):
    """在节点上取消注册解释器"""
    result = await _proxy_node_request(
        node_id,
        "DELETE",
        f"/envs/python/interpreters/{version}",
        params={"source": source},
        current_user=current_user,
    )
    return success({"deleted": True, "version": version})


@router.get(
    "/{node_id}/python-versions",
    response_model=BaseResponse[dict],
    summary="获取节点Python版本信息",
    description="获取指定节点上已安装和可安装的Python版本"
)
async def get_node_python_versions(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点Python版本信息"""
    result = await _proxy_node_request(
        node_id,
        "GET",
        "/envs/python/versions",
        current_user=current_user,
        require_permission="view",
    )
    return success(result)


@router.post(
    "/{node_id}/python-versions/{version}/install",
    response_model=BaseResponse[dict],
    summary="在节点上安装Python版本",
    description="通过mise在指定节点上安装Python版本"
)
async def install_node_python_version(
    node_id: str,
    version: str,
    current_user: TokenData = Depends(get_current_user)
):
    """在节点上安装Python版本"""
    result = await _proxy_node_request(
        node_id,
        "POST",
        f"/envs/python/versions/{version}/install",
        timeout=1800,
        current_user=current_user,
    )
    return success(result)


@router.get(
    "/{node_id}/platform",
    response_model=BaseResponse[dict],
    summary="获取节点平台信息",
    description="获取指定节点的操作系统和环境信息"
)
async def get_node_platform(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """获取节点平台信息"""
    result = await _proxy_node_request(
        node_id,
        "GET",
        "/envs/platform",
        current_user=current_user,
        require_permission="view",
    )
    return success(result)





# ====== 节点资源管理 API（管理员功能）======

@router.get(
    "/{node_id}/resources",
    response_model=BaseResponse[dict],
    summary="获取节点资源限制",
    description="获取指定节点的资源限制和监控状态（需要管理员权限）"
)
async def get_node_resources(
    node_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """
    获取节点资源限制（管理员可查看）
    
    返回:
    - limits: 当前资源限制配置
    - auto_adjustment: 是否启用自适应调整
    - resource_stats: 实时资源统计
    """
    from src.models import User

    # 检查管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限查看资源配置"
        )

    result = await _proxy_node_request(
        node_id,
        "GET",
        "/node/resources",
        current_user=current_user,
    )
    return success(result)


@router.post(
    "/{node_id}/resources",
    response_model=BaseResponse[dict],
    summary="调整节点资源限制",
    description="手动调整指定节点的资源限制（仅超级管理员）"
)
async def update_node_resources(
    node_id: str,
    request: dict = Body(...),
    current_super_admin: TokenData = Depends(get_current_super_admin)
):
    """
    调整节点资源限制（仅 admin 用户可修改）
    
    参数:
    - max_concurrent_tasks: 最大并发任务数 (1-20)
    - task_memory_limit_mb: 单任务内存限制 (256-8192 MB)
    - task_cpu_time_limit_sec: 单任务CPU时间限制 (60-3600 秒)
    - auto_resource_limit: 是否启用自适应资源限制
    """
    # 参数验证
    max_concurrent = request.get("max_concurrent_tasks")
    memory_limit = request.get("task_memory_limit_mb")
    cpu_limit = request.get("task_cpu_time_limit_sec")
    auto_limit = request.get("auto_resource_limit")

    # 范围校验
    if max_concurrent is not None and not (1 <= max_concurrent <= 20):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="最大并发任务数必须在 1-20 之间"
        )
    if memory_limit is not None and not (256 <= memory_limit <= 8192):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="单任务内存限制必须在 256-8192 MB 之间"
        )
    if cpu_limit is not None and not (60 <= cpu_limit <= 3600):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="单任务CPU时间限制必须在 60-3600 秒之间"
        )

    # 构建请求参数
    params = {}
    if max_concurrent is not None:
        params["max_concurrent_tasks"] = max_concurrent
    if memory_limit is not None:
        params["task_memory_limit_mb"] = memory_limit
    if cpu_limit is not None:
        params["task_cpu_time_limit_sec"] = cpu_limit
    if auto_limit is not None:
        params["auto_resource_limit"] = auto_limit

    if not params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="至少需要提供一个配置项"
        )

    result = await _proxy_node_request(
        node_id,
        "POST",
        "/node/resources",
        params=params,
        current_user=current_user,
    )

    logger.info(f"超级管理员 {user.username} 调整了节点 {node_id} 的资源限制: {params}")

    return success(result, message="资源限制已更新")
