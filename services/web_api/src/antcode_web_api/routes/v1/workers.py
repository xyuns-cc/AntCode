"""Worker 管理 API"""

import asyncio
import json
import os
import time
from datetime import UTC
from ipaddress import ip_address, ip_network
from typing import Any, NoReturn

from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.workers import worker_service
from antcode_core.common.config import settings
from antcode_core.common.exceptions import RedisConnectionError
from antcode_core.common.security.api_key import store_api_key, store_secret_key
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.common.security.network_source import extract_client_ip
from antcode_core.common.security.worker_auth import (
    verify_worker_request_with_signature,
)
from antcode_core.domain.models import (
    DispatchStatus,
    Task,
    TaskRun,
    TaskStatus,
    User,
    UserRole,
    Worker,
    WorkerInstallKey,
    WorkerStatus,
)
from antcode_core.domain.models.audit_log import AuditAction
from antcode_core.domain.schemas.worker import (
    WorkerAggregateStats,
    WorkerCapabilities,
    WorkerCreateRequest,
    WorkerHeartbeatRequest,
    WorkerInstallKeyRequest,
    WorkerInstallKeyResponse,
    WorkerListResponse,
    WorkerMetrics,
    WorkerRegisterByKeyRequest,
    WorkerRegisterDirectRequest,
    WorkerRegisterDirectResponse,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
    WorkerResponse,
    WorkerTestConnectionResponse,
    WorkerUpdateRequest,
)
from antcode_core.infrastructure.redis import (
    build_config_update_control_payload,
    control_stream,
    direct_register_proof_key,
    get_redis_client,
    install_key_redis_digest,
    worker_install_key_block_key,
    worker_install_key_claim_key,
    worker_install_key_fail_counter_key,
    worker_install_key_meta_key,
    worker_install_key_nonce_key,
    worker_install_source_block_key,
    worker_install_source_fail_counter_key,
)
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from tortoise.expressions import Q

from antcode_web_api.deps import require_role
from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routing import promote_static_routes
from antcode_web_api.services.worker_installer import (
    WorkerInstallCommandRequest,
    WorkerInstallerConfigurationError,
    build_worker_install_command,
    load_worker_install_config,
)
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids

router = APIRouter()

MAX_LOG_LINE_CHARS = 1_048_576
MAX_LOG_BATCH_ENTRIES = 1_000


class _WorkerReportBaseModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class WorkerTaskLogReportRequest(_WorkerReportBaseModel):
    run_id: str = Field(..., min_length=1, description="任务运行 ID")
    log_type: str = Field(default="stdout", description="日志类型")
    content: str = Field(..., min_length=1, max_length=MAX_LOG_LINE_CHARS, description="日志内容")


class WorkerTaskLogsBatchReportRequest(_WorkerReportBaseModel):
    logs: list[WorkerTaskLogReportRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_LOG_BATCH_ENTRIES,
        description="批量日志条目",
    )


class WorkerTaskHeartbeatReportRequest(_WorkerReportBaseModel):
    run_id: str = Field(..., min_length=1, description="任务运行 ID")


class WorkerTaskStatusReportRequest(_WorkerReportBaseModel):
    run_id: str = Field(..., min_length=1, description="任务运行 ID")
    status: str = Field(..., min_length=1, description="任务状态")
    exit_code: int | None = Field(default=None, description="任务退出码")
    error_message: str | None = Field(default=None, description="错误信息")


_MAX_DISPATCH_BATCH_TASKS = 500
_MAX_DISPATCH_TIMEOUT_SECONDS = 86400


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    def get(self, key: str, default=None):
        value = getattr(self, key, default)
        return default if value is None else value


class WorkerDispatchTaskRequest(_StrictRequestModel):
    project_id: str = Field(..., min_length=1, max_length=64)
    task_id: int = Field(..., gt=0)
    params: dict = Field(default_factory=dict)
    environment_vars: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=3600, ge=1, le=_MAX_DISPATCH_TIMEOUT_SECONDS)
    worker_id: str | None = Field(default=None, min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=32)
    priority: int | None = Field(default=None, ge=0, le=4)
    project_type: str = Field(default="code", pattern="^(code|file|rule)$")
    require_render: bool = False


class WorkerDispatchBatchTaskRequest(_StrictRequestModel):
    project_id: str = Field(..., min_length=1, max_length=64)
    task_id: int = Field(..., gt=0)
    run_id: str = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=3600, ge=1, le=_MAX_DISPATCH_TIMEOUT_SECONDS)
    priority: int | None = Field(default=None, ge=0, le=4)
    project_type: str = Field(default="code", pattern="^(code|file|rule)$")
    require_render: bool = False


class WorkerDispatchBatchRequest(_StrictRequestModel):
    tasks: list[WorkerDispatchBatchTaskRequest] = Field(
        ...,
        min_length=1,
        max_length=_MAX_DISPATCH_BATCH_TASKS,
    )
    worker_id: str | None = Field(default=None, min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=32)
    batch_id: str | None = Field(default=None, max_length=128)
    require_render: bool = False


def _extract_request_source(request: Request) -> str:
    """提取请求来源 IP。

    XFF / X-Real-IP 只有当 socket 对端 IP 命中 ``ANTCODE_TRUSTED_PROXIES``
    白名单时才被信任,否则一律以 socket 对端 IP 为准;这样可以避免任意客户端
    通过伪造 ``X-Forwarded-For`` 头绕过基于 IP 的失败计数器 / 来源绑定。
    从右向左剥离可信代理，避免客户端预置伪造的最左侧 XFF 值。
    """
    direct = request.client.host if request.client and request.client.host else ""
    if not direct:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法确定注册请求来源 IP")
    try:
        return extract_client_ip(
            direct,
            request.headers.get("X-Forwarded-For", ""),
            request.headers.get("X-Real-IP", ""),
            trusted_proxies=os.getenv("ANTCODE_TRUSTED_PROXIES", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _is_source_match(source: str, rule: str) -> bool:
    source_value = (source or "").strip().lower()
    rule_value = (rule or "").strip().lower()
    if not rule_value:
        return True
    if not source_value:
        return False

    if "/" in rule_value:
        try:
            return ip_address(source_value) in ip_network(rule_value, strict=False)
        except ValueError:
            return False

    try:
        return ip_address(source_value) == ip_address(rule_value)
    except ValueError:
        return False


async def _check_install_key_blocked(
    key: str,
    source: str,
) -> tuple[bool, int]:
    redis = await get_redis_client()
    block_key = worker_install_key_block_key(key, source)
    source_block_key = worker_install_source_block_key(source)
    key_ttl = int(await redis.ttl(block_key) or 0)
    source_ttl = int(await redis.ttl(source_block_key) or 0)
    ttl = max(key_ttl, source_ttl, 0)
    return ttl > 0, ttl


async def _record_install_key_failed_attempt(
    key: str,
    source: str,
) -> int:
    redis = await get_redis_client()
    fail_counter_key = worker_install_key_fail_counter_key(key, source)
    source_counter_key = worker_install_source_fail_counter_key(source)
    dimensions = (
        (fail_counter_key, worker_install_key_block_key(key, source)),
        (source_counter_key, worker_install_source_block_key(source)),
    )
    counts: list[int] = []
    for counter_key, block_key in dimensions:
        count = int(await redis.incr(counter_key))
        counts.append(count)
        if count == 1:
            await redis.expire(counter_key, settings.WORKER_INSTALL_KEY_BLOCK_SECONDS)
        if count >= settings.WORKER_INSTALL_KEY_FAIL_THRESHOLD:
            await redis.set(block_key, "1", ex=settings.WORKER_INSTALL_KEY_BLOCK_SECONDS)

    highest_count = max(counts)
    log_args = (
        source,
        install_key_redis_digest(key)[:16],
        highest_count,
        settings.WORKER_INSTALL_KEY_FAIL_THRESHOLD,
    )
    if highest_count >= settings.WORKER_INSTALL_KEY_FAIL_THRESHOLD:
        logger.error(
            "Worker install-key 来源封禁已触发: source={} key_digest={} failures={} threshold={}",
            *log_args,
        )
    else:
        logger.warning(
            "Worker install-key 校验失败: source={} key_digest={} failures={} threshold={}",
            *log_args,
        )
    return highest_count


async def _clear_install_key_fail_counter(key: str, source: str) -> None:
    redis = await get_redis_client()
    await redis.delete(
        worker_install_key_fail_counter_key(key, source),
        worker_install_source_fail_counter_key(source),
    )


async def _claim_install_key_source_once(
    key: str,
    source: str,
    request_timestamp: int,
    request_nonce: str,
) -> tuple[bool, str]:
    redis = await get_redis_client()
    claim_key = worker_install_key_claim_key(key)
    nonce_key = worker_install_key_nonce_key(key, request_nonce)

    nonce_set = await redis.set(
        nonce_key,
        "1",
        ex=settings.WORKER_INSTALL_KEY_REPLAY_WINDOW_SECONDS,
        nx=True,
    )
    if not nonce_set:
        return False, "请求重复（nonce 已使用）"

    current_ts = int(time.time())
    if abs(current_ts - int(request_timestamp)) > settings.WORKER_INSTALL_KEY_REPLAY_WINDOW_SECONDS:
        return False, "请求已过期"

    existing_source = await redis.get(claim_key)
    if existing_source:
        existing_value = (
            existing_source.decode("utf-8") if isinstance(existing_source, (bytes, bytearray)) else str(existing_source)
        )
        if existing_value != source:
            return False, "安装 Key 已绑定其它来源"
        return True, "ok"

    set_ok = await redis.set(
        claim_key,
        source,
        ex=settings.WORKER_INSTALL_KEY_REPLAY_WINDOW_SECONDS,
        nx=True,
    )
    if set_ok:
        return True, "ok"

    existing_source = await redis.get(claim_key)
    if existing_source:
        existing_value = (
            existing_source.decode("utf-8") if isinstance(existing_source, (bytes, bytearray)) else str(existing_source)
        )
        if existing_value != source:
            return False, "安装 Key 已绑定其它来源"
    return True, "ok"


class _InstallKeyClaimConflict(Exception):
    """安装 Key 在事务内已被其他请求消费或已经过期。"""


async def _create_worker_from_install_key(request, install_key, request_source: str):
    """在单个数据库事务中消费安装 Key 并创建 Worker。"""
    import secrets

    from antcode_core.domain.models import Worker, WorkerInstallKey
    from tortoise.transactions import in_transaction

    placeholder_public_id = f"pending:{secrets.token_hex(8)}"
    api_key = secrets.token_hex(16)
    secret_key = secrets.token_hex(32)

    async with in_transaction("default") as connection:
        claimed = await WorkerInstallKey.cas_claim_pending(
            request.key,
            placeholder_public_id,
            allowed_source=(install_key.allowed_source or request_source),
            using_db=connection,
        )
        if not claimed:
            raise _InstallKeyClaimConflict

        worker = Worker(
            name=request.name,
            host=request.host,
            port=request.port,
            region=request.region or "",
            status="connecting",
            created_by=install_key.created_by,
            transport_mode=request.transport_mode,
        )
        store_api_key(worker, api_key)
        store_secret_key(worker, secret_key)
        await worker.save(using_db=connection)

        updated = await WorkerInstallKey.finalize_claim(
            request.key,
            placeholder_public_id,
            worker.public_id,
            using_db=connection,
        )
        if updated != 1:
            raise RuntimeError("安装 Key 真实 Worker ID 回写失败，事务已回滚")

    return worker, api_key, secret_key


async def _verify_worker_credential_headers(
    request: Request,
    auth_info: dict = Depends(verify_worker_request_with_signature),
) -> dict:
    """校验 Worker 凭证头（签名 + Worker ID + API Key）。"""
    worker_id = (auth_info.get("worker_id") or "").strip()
    if not worker_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Worker 标识")

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Worker 不存在")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少认证信息")
    api_key = auth_header[7:].strip()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 API Key")

    if not await worker_service.verify_api_key(worker, api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 API Key")

    return {"worker": worker, "auth_info": auth_info}


def _mask_redis_url(redis_url: str) -> str:
    if "@" not in redis_url:
        return redis_url
    prefix, suffix = redis_url.split("@", 1)
    if ":" in prefix:
        prefix = prefix.rsplit(":", 1)[0] + ":***"
    return f"{prefix}@{suffix}"


async def _request_user(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不可用")
    return user


async def _require_worker_access(
    worker_id: str,
    current_user: TokenData,
    required_permission: str = "view",
) -> Worker:
    worker = await worker_service.get_worker_by_id(worker_id)
    if worker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    user = await _request_user(current_user)
    allowed = await worker_service.check_user_worker_permission(
        user_id=user.id,
        worker_id=worker.id,
        is_admin=user.is_admin,
        required_permission=required_permission,
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return worker


async def _list_accessible_workers(
    current_user: TokenData,
    *,
    page: int,
    size: int,
    status_filter: str | None,
    region: str | None,
    search: str | None,
) -> tuple[list[Worker], int]:
    user = await _request_user(current_user)
    if user.is_admin:
        return await worker_service.get_workers(
            page=page,
            size=size,
            status_filter=status_filter,
            region=region,
            search=search,
        )
    workers = await worker_service.get_user_workers(user_id=user.id, is_admin=False)
    search_value = (search or "").strip().lower()
    filtered = [
        worker
        for worker in workers
        if (not status_filter or worker.status == status_filter)
        and (not region or worker.region == region)
        and (
            not search_value
            or search_value in (worker.name or "").lower()
            or search_value in (worker.host or "").lower()
            or search_value in (worker.description or "").lower()
        )
    ]
    offset = (page - 1) * size
    return filtered[offset : offset + size], len(filtered)


async def _require_run_access(run_id: str, current_user: TokenData) -> None:
    user = await _request_user(current_user)
    if user.is_admin:
        return
    execution = await TaskRun.filter(Q(run_id=run_id) | Q(public_id=run_id)).first()
    task = await Task.get_or_none(id=execution.task_id) if execution else None
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")


async def _resolve_dispatch_worker(
    requested_worker_id: str | None,
    current_user: TokenData,
) -> str | None:
    user = await _request_user(current_user)
    if user.is_admin:
        return requested_worker_id
    if not requested_worker_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="普通用户分发任务时必须显式指定已授权 Worker",
        )
    worker = await _require_worker_access(requested_worker_id, current_user, "use")
    return worker.public_id


def _worker_to_response(worker) -> WorkerResponse:
    """将 Worker 模型转换为响应对象"""
    metrics = WorkerMetrics()
    if worker.metrics:
        try:
            metrics = WorkerMetrics(**worker.metrics)
        except Exception:
            metrics = WorkerMetrics()

    # 解析 Worker 能力
    capabilities = WorkerCapabilities()
    if worker.capabilities:
        try:
            capabilities = WorkerCapabilities(**worker.capabilities)
        except Exception:
            capabilities = WorkerCapabilities()

    # 处理时间字段，转换为 ISO 格式字符串
    last_heartbeat = ""
    if worker.last_heartbeat:
        last_heartbeat = (
            worker.last_heartbeat.isoformat()
            if hasattr(worker.last_heartbeat, "isoformat")
            else str(worker.last_heartbeat)
        )

    updated_at = ""
    if worker.updated_at:
        updated_at = (
            worker.updated_at.isoformat() if hasattr(worker.updated_at, "isoformat") else str(worker.updated_at)
        )

    return WorkerResponse(
        id=worker.public_id,
        name=worker.name,
        host=worker.host,
        port=worker.port,
        status=worker.status,
        region=worker.region or "",
        description=worker.description or "",
        tags=worker.tags or [],
        version=worker.version or "",
        # 操作系统信息
        osType=getattr(worker, "os_type", None) or "",
        osVersion=getattr(worker, "os_version", None) or "",
        pythonVersion=getattr(worker, "python_version", None) or "",
        machineArch=getattr(worker, "machine_arch", None) or "",
        # 连接模式
        transportMode=getattr(worker, "transport_mode", None),
        # Worker 能力
        capabilities=capabilities,
        metrics=metrics,
        lastHeartbeat=last_heartbeat,
        createdAt=worker.created_at,
        updatedAt=updated_at,
    )


@router.get(
    "",
    response_model=BaseResponse[WorkerListResponse],
    summary="获取 Worker 列表",
    description="获取所有 Worker 列表，支持分页和过滤",
)
async def get_workers(
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    status_filter: str | None = Query(None, alias="status", description="状态过滤"),
    region: str | None = Query(None, description="区域过滤"),
    search: str | None = Query(None, description="搜索关键词"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取 Worker 列表"""
    workers, total = await _list_accessible_workers(
        current_user,
        page=page,
        size=size,
        status_filter=status_filter,
        region=region,
        search=search,
    )

    items = [_worker_to_response(worker) for worker in workers]

    return success(WorkerListResponse(items=items, total=total, page=page, size=size))


@router.get(
    "/stats",
    response_model=BaseResponse[WorkerAggregateStats],
    summary="获取 Worker 统计",
    description="获取所有 Worker 的聚合统计信息",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_worker_stats(current_user: TokenData = Depends(get_current_user)):
    """获取 Worker 统计信息"""
    stats = await worker_service.get_aggregate_stats()
    return success(stats)


@router.get(
    "/cluster/metrics/history",
    response_model=BaseResponse[dict],
    summary="获取集群历史指标",
    description="获取所有 Worker 的聚合历史指标",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_cluster_metrics_history(
    hours: int = Query(24, ge=1, le=720, description="查询时间范围（小时）"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取集群历史指标"""
    history = await worker_service.get_cluster_metrics_history(hours=hours)
    return success(history)


@router.post(
    "",
    response_model=BaseResponse[WorkerResponse],
    summary="创建 Worker",
    description="手动创建新的 Worker（不推荐，建议使用安装 Key 注册）",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def create_worker(
    request: WorkerCreateRequest,
    http_request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    """创建 Worker"""
    from antcode_core.application.services.users.user_service import user_service

    worker = await worker_service.create_worker(request, current_user.user_id)

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log(
        action=AuditAction.WORKER_CREATE,
        resource_type="worker",
        username=user.username if user else "unknown",
        resource_id=worker.public_id,
        resource_name=worker.name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"创建 Worker: {worker.name}",
    )

    return success(_worker_to_response(worker), message="Worker 创建成功")


@router.get(
    "/{worker_id}/credentials",
    response_model=BaseResponse[dict],
    summary="获取 Worker 凭证",
    description="Worker 凭证不可恢复，仅在注册响应中返回一次",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_worker_credentials(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """拒绝恢复性读取，凭据只能在注册时一次性取得。"""
    await _require_worker_access(worker_id, current_user)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Worker 凭据不可恢复，请使用新的安装 Key 重新注册",
    )


@router.post(
    "/{worker_id}/disconnect",
    response_model=BaseResponse[dict],
    summary="断开 Worker",
    description="断开与 Worker 的连接",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def disconnect_worker(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """断开 Worker 连接"""
    result = await worker_service.disconnect_worker(worker_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return success({"disconnected": True}, message="Worker 已断开")


@router.get(
    "/{worker_id}",
    response_model=BaseResponse[WorkerResponse],
    summary="获取 Worker 详情",
    description="根据ID获取 Worker 详细信息",
)
async def get_worker(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取 Worker 详情"""
    worker = await _require_worker_access(worker_id, current_user)
    return success(_worker_to_response(worker))


@router.put(
    "/{worker_id}",
    response_model=BaseResponse[WorkerResponse],
    summary="更新 Worker",
    description="更新 Worker 信息",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def update_worker(
    worker_id: str,
    request: WorkerUpdateRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """更新 Worker"""
    worker = await worker_service.update_worker(worker_id, request)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return success(_worker_to_response(worker), message="Worker 更新成功")


@router.delete(
    "/{worker_id}",
    response_model=BaseResponse[dict],
    summary="删除 Worker",
    description="删除指定 Worker",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def delete_worker(
    worker_id: str,
    http_request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    """删除 Worker"""
    from antcode_core.application.services.users.user_service import user_service

    # 获取 Worker 信息用于审计
    worker = await worker_service.get_worker_by_id(worker_id)
    worker_name = worker.name if worker else worker_id

    deleted = await worker_service.delete_worker(worker_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user.user_id)
    await audit_service.log(
        action=AuditAction.WORKER_DELETE,
        resource_type="worker",
        username=user.username if user else "unknown",
        resource_id=worker_id,
        resource_name=worker_name,
        user_id=current_user.user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"删除 Worker: {worker_name}",
    )

    return success({"deleted": True}, message="Worker 删除成功")


@router.post(
    "/batch-delete",
    response_model=BaseResponse[dict],
    summary="批量删除 Worker",
    description="批量删除多个 Worker",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def batch_delete_workers(request: dict = Body(...), current_user: TokenData = Depends(get_current_user)):
    """批量删除 Worker"""
    worker_ids = bounded_distinct_ids(request.get("worker_ids"), "worker_ids")

    result = await worker_service.batch_delete_workers(worker_ids)

    if result["failed_count"] == 0:
        message = f"成功删除 {result['success_count']} 个 Worker"
    elif result["success_count"] == 0:
        message = f"删除失败，{result['failed_count']} 个 Worker 删除失败"
    else:
        message = f"部分成功：{result['success_count']} 个删除成功，{result['failed_count']} 个失败"

    return success(result, message=message)


@router.post(
    "/{worker_id}/test",
    response_model=BaseResponse[WorkerTestConnectionResponse],
    summary="测试 Worker 连接",
    description="测试与 Worker 的网络连接",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def test_worker_connection(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """测试 Worker 连接"""
    result = await worker_service.test_connection(worker_id)
    return success(WorkerTestConnectionResponse(**result))


@router.post(
    "/{worker_id}/refresh",
    response_model=BaseResponse[WorkerResponse],
    summary="刷新 Worker 状态",
    description="重新检测并更新 Worker 状态",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def refresh_worker_status(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """刷新 Worker 状态"""
    worker = await worker_service.refresh_worker_status(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
    return success(_worker_to_response(worker))


# ====== Worker 权限管理 API（需要管理员权限）======


@router.get(
    "/my/available",
    response_model=BaseResponse[WorkerListResponse],
    summary="获取我可用的 Worker",
    description="获取当前用户有权限访问的 Worker 列表",
)
async def get_my_available_workers(current_user: TokenData = Depends(get_current_user)):
    """获取当前用户可用的 Worker 列表"""
    from antcode_core.domain.models import User

    # 从数据库获取用户信息
    user = await User.get_or_none(id=current_user.user_id)
    is_admin = user.is_admin if user else False

    workers = await worker_service.get_user_workers(user_id=current_user.user_id, is_admin=is_admin)

    items = [_worker_to_response(worker) for worker in workers]

    return success(WorkerListResponse(items=items, total=len(items), page=1, size=len(items)))


@router.get(
    "/{worker_id}/users",
    response_model=BaseResponse[list],
    summary="获取 Worker 授权用户",
    description="获取该 Worker 的授权用户列表（管理员）",
)
async def get_worker_users(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取 Worker 的授权用户列表"""
    from antcode_core.domain.models import User

    # 从数据库获取用户信息以检查管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    users = await worker_service.get_worker_users(worker.id)
    return success(users)


@router.post(
    "/{worker_id}/assign",
    response_model=BaseResponse[dict],
    summary="分配 Worker 权限",
    description="给用户分配 Worker 访问权限（管理员）",
)
async def assign_worker_permission(
    worker_id: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user),
):
    """分配 Worker 权限给用户"""
    from antcode_core.domain.models import User

    # 从数据库获取用户信息以检查管理员权限
    admin_user = await User.get_or_none(id=current_user.user_id)
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    user_id = request.get("user_id")
    permission = request.get("permission", "use")
    note = request.get("note")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户ID不能为空")

    # 支持 public_id 或内部 id
    if isinstance(user_id, str):
        user = await User.filter(public_id=user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        user_id = user.id

    await worker_service.assign_worker_to_user(
        worker_id=worker.id,
        user_id=user_id,
        permission=permission,
        assigned_by=current_user.user_id,
        note=note,
    )

    return success({"assigned": True}, message="权限分配成功")


@router.delete(
    "/{worker_id}/revoke/{user_id}",
    response_model=BaseResponse[dict],
    summary="撤销 Worker 权限",
    description="撤销用户的 Worker 访问权限（管理员）",
)
async def revoke_worker_permission(worker_id: str, user_id: str, current_user: TokenData = Depends(get_current_user)):
    """撤销用户的 Worker 权限"""
    from antcode_core.domain.models import User

    # 从数据库获取用户信息以检查管理员权限
    admin_user = await User.get_or_none(id=current_user.user_id)
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    # 支持 public_id 或内部 id
    try:
        internal_user_id = int(user_id)
    except ValueError:
        # 是 public_id
        user = await User.filter(public_id=user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        internal_user_id = user.id

    revoked = await worker_service.revoke_worker_from_user(worker.id, internal_user_id)

    if revoked:
        return success({"revoked": True}, message="权限撤销成功")
    else:
        return success({"revoked": False}, message="该用户没有此 Worker 权限")


@router.post(
    "/batch-assign",
    response_model=BaseResponse[dict],
    summary="批量分配 Worker 权限",
    description="批量给用户分配多个 Worker 权限（管理员）",
)
async def batch_assign_workers(request: dict = Body(...), current_user: TokenData = Depends(get_current_user)):
    """批量分配 Worker 权限"""
    from antcode_core.domain.models import User, Worker

    # 从数据库获取用户信息以检查管理员权限
    admin_user = await User.get_or_none(id=current_user.user_id)
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    user_id = request.get("user_id")
    worker_ids = bounded_distinct_ids(request.get("worker_ids"), "worker_ids")
    permission = request.get("permission", "use")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户ID不能为空")

    # 批量获取 Worker 的内部ID，避免 N+1 查询
    # 支持 public_id 和内部 ID 混合查询
    int_ids = []
    str_ids = []
    for wid in worker_ids:
        if isinstance(wid, int) or (isinstance(wid, str) and wid.isdigit()):
            int_ids.append(int(wid) if isinstance(wid, str) else wid)
        else:
            str_ids.append(wid)

    # 合并成一次查询：Q(id__in=int_ids) | Q(public_id__in=str_ids)
    from tortoise.expressions import Q as _Q

    conditions = _Q()
    if int_ids:
        conditions |= _Q(id__in=int_ids)
    if str_ids:
        conditions |= _Q(public_id__in=str_ids)
    workers_matched = await Worker.filter(conditions).only("id").all() if (int_ids or str_ids) else []
    internal_ids = [w.id for w in workers_matched]

    result = await worker_service.batch_assign_workers(
        user_id=user_id,
        worker_ids=internal_ids,
        permission=permission,
        assigned_by=current_user.user_id,
    )

    return success(result, message=f"成功分配 {result['success']} 个 Worker 权限")


# ====== Worker 端调用的 API（无需用户认证）======


@router.get(
    "/{worker_id}/metrics/history",
    response_model=BaseResponse[list],
    summary="获取 Worker 历史指标",
    description="获取 Worker 的历史指标数据用于图表展示",
)
async def get_worker_metrics_history(
    worker_id: str,
    hours: int = Query(24, ge=1, le=720, description="查询时间范围（小时）"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取 Worker 历史指标"""
    worker = await _require_worker_access(worker_id, current_user)

    history = await worker_service.get_metrics_history(worker.id, hours=hours)
    return success(history)


@router.post(
    "/register-direct",
    response_model=BaseResponse[WorkerRegisterDirectResponse],
    summary="Direct Worker 注册",
    description="仅供未启用 Redis ACL 的可信内网 Direct Worker 使用 Redis 证明注册",
)
async def register_direct_worker(
    request: WorkerRegisterDirectRequest,
):
    """Direct Worker 注册（worker_id 作为 public_id）"""
    from antcode_core.common.config import settings
    from antcode_core.infrastructure.redis import get_redis_client

    if settings.REDIS_ACL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Redis ACL 已启用，请使用安装 Key 注册并通过签名接口签发 Redis 凭据",
        )
    if not settings.REDIS_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Direct 注册需要 Redis 支持",
        )

    try:
        redis = await get_redis_client()
    except RedisConnectionError as exc:
        logger.exception("Direct 注册 Redis 连接失败")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Direct 注册依赖服务暂不可用",
        ) from exc
    proof_key = direct_register_proof_key(request.worker_id)
    # P1-08: 原子消费 Direct 注册证明，避免两个并发注册请求同时读到同一条 proof
    # 之后各自成功。redis-py 5+ 的 async getdel() 一次 RTT 完成 GET+DEL。
    try:
        stored_proof = await redis.getdel(proof_key)
    except Exception as exc:
        logger.exception("Direct 注册读取 Redis 证明失败: worker_id={}", request.worker_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Direct 注册依赖服务暂不可用",
        ) from exc
    if isinstance(stored_proof, (bytes, bytearray)):
        stored_proof = stored_proof.decode("utf-8")
    if not stored_proof or stored_proof != request.proof:
        logger.warning(
            "Direct 注册证明无效: worker_id={}, redis={}, exists={}",
            request.worker_id,
            _mask_redis_url(settings.REDIS_URL),
            bool(stored_proof),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无效的 Direct 注册证明",
        )

    try:
        worker, created = await worker_service.register_direct_worker(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return success(
        WorkerRegisterDirectResponse(
            worker_id=worker.public_id,
            created=created,
            redis_username=None,
            redis_password=None,
        ),
        message="Direct Worker 注册成功",
    )


@router.post(
    "/{worker_id}/redis-acl/issue",
    response_model=BaseResponse[dict],
    summary="签发 Direct Worker Redis ACL",
)
async def issue_worker_redis_acl(
    worker_id: str,
    auth_context: dict = Depends(_verify_worker_credential_headers),
):
    """Rotate Redis credentials for the signed, path-bound Direct Worker."""
    from antcode_core.common.config import settings
    from antcode_core.common.security.redis_acl import ensure_worker_acl
    from antcode_core.infrastructure.redis import get_redis_client

    worker = auth_context["worker"]
    if worker.public_id != worker_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker 身份与路径不匹配")
    if worker.transport_mode != "direct":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker 未注册为 Direct 模式，拒绝签发 Redis ACL",
        )
    if not settings.REDIS_ACL_ENABLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Redis ACL 未启用")
    if not await _is_registration_acknowledged(worker.public_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Worker V2 注册尚未完成 ACK，拒绝签发 Redis ACL",
        )
    redis = await get_redis_client()
    if redis is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis ACL 服务不可用")
    password = await ensure_worker_acl(redis, worker)
    return success(
        {"redis_username": worker.redis_username, "redis_password": password},
        message="Redis ACL 签发成功",
    )


async def _is_registration_acknowledged(worker_id: str) -> bool:
    return await WorkerInstallKey.filter(
        status="used",
        used_by_worker=worker_id,
        registration_acknowledged_at__isnull=False,
    ).exists()


@router.post(
    "/register",
    response_model=BaseResponse[WorkerRegisterResponse],
    summary="Worker 注册",
    description="Worker 主动注册到平台",
)
async def register_worker(request: WorkerRegisterRequest):
    """Worker 注册（已废弃，统一使用安装 Key 或 Direct 注册）"""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="该注册方式已下线，请使用 /workers/register-by-key 或 /workers/register-direct",
    )


@router.post(
    "/generate-install-key",
    response_model=BaseResponse[WorkerInstallKeyResponse],
    summary="生成 Worker 安装 Key",
    description="生成一次性安装 Key，用于 Worker 快速注册（管理员）",
)
async def generate_install_key(
    request: WorkerInstallKeyRequest,
    http_request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    """生成 Worker 安装 Key

    生成一次性安装命令，复制到目标机器执行即可完成 Worker 注册。
    类似 nezha 探针的工作模式。
    """
    from antcode_core.domain.models import User, WorkerInstallKey

    # 检查管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    # 验证操作系统类型
    os_type = request.os_type.lower()
    if os_type not in ("linux", "macos", "windows"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="操作系统类型必须是 linux、macos 或 windows",
        )

    allowed_source = (request.allowed_source or "").strip()

    try:
        install_config = load_worker_install_config(settings)
    except WorkerInstallerConfigurationError as exc:
        logger.exception("Worker 安装分发配置无效")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Worker 安装分发未正确配置",
        ) from exc

    # 创建安装 Key
    install_key = await WorkerInstallKey.create_install_key(
        os_type=os_type,
        created_by=current_user.user_id,
        allowed_source=allowed_source or None,
    )
    # P2-11：明文只在生成时返回给用户一次，DB 只存 hash。
    plaintext_key: str = getattr(install_key, "plaintext_key", "")
    if not plaintext_key:
        raise HTTPException(status_code=500, detail="生成安装 Key 失败：明文缺失")

    redis = await get_redis_client()
    # 用明文构造 meta_key，与查询侧一致（客户端上报的也是明文）
    meta_key = worker_install_key_meta_key(plaintext_key)
    now_ts = int(time.time())
    ttl_seconds = max(int(install_key.expires_at.timestamp()) - now_ts, 1)
    meta_payload = {
        "allowed_source": allowed_source,
        "created_at": now_ts,
    }
    await redis.set(meta_key, json.dumps(meta_payload), ex=ttl_seconds)

    install_command = build_worker_install_command(
        WorkerInstallCommandRequest(os_type=os_type, install_key=plaintext_key),
        install_config,
    )

    return success(
        WorkerInstallKeyResponse(
            key=plaintext_key,
            os_type=os_type,
            allowed_source=allowed_source or None,
            install_command=install_command,
            expires_at=install_key.expires_at,
        ),
        message="安装命令已生成，请复制到目标机器执行",
    )


@router.post(
    "/register-by-key",
    response_model=BaseResponse[WorkerRegisterResponse],
    summary="使用 Key 注册 Worker",
    description="Worker 使用安装 Key 进行注册（无需认证）",
)
async def register_worker_by_key(request: WorkerRegisterByKeyRequest, http_request: Request):
    """Worker 使用安装 Key 注册

    Worker 启动时通过环境变量获取 Key，调用此接口完成注册。
    """
    from antcode_core.domain.models import WorkerInstallKey

    if not request.client_nonce or len(request.client_nonce.strip()) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少有效的 client_nonce")

    request_source = _extract_request_source(http_request)

    is_blocked, block_ttl = await _check_install_key_blocked(request.key, request_source)
    if is_blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"注册尝试过于频繁，请 {block_ttl} 秒后重试",
        )

    # 查找并验证 Key
    # P2-11：DB 只存 SHA-256(明文)，查找时对明文再 hash 后按 hash 查询；
    # 应用层再补一次恒定时间比对（用 hash 值），仍保留旁路侧防护。
    install_key = await WorkerInstallKey.find_by_plaintext(request.key)
    if install_key and not WorkerInstallKey.matches_plaintext(install_key.key, request.key):
        # 极端情况下:ORM 命中但常量时间比对不一致(理论上不会发生),按未命中处理。
        install_key = None
    if not install_key:
        await _record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="安装 Key 不存在")

    if not install_key.is_valid():
        await _record_install_key_failed_attempt(request.key, request_source)
        if install_key.status == "used":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="此安装 Key 已被使用",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="安装 Key 已过期",
        )

    allowed_source = (install_key.allowed_source or "").strip()
    if allowed_source and not _is_source_match(request_source, allowed_source):
        await _record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="来源不在安装 Key 允许范围内",
        )

    claim_ok, claim_message = await _claim_install_key_source_once(
        key=request.key,
        source=request_source,
        request_timestamp=request.client_timestamp,
        request_nonce=request.client_nonce,
    )
    if not claim_ok:
        await _record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=claim_message)

    try:
        worker, api_key, secret_key = await _create_worker_from_install_key(
            request,
            install_key,
            request_source,
        )
    except _InstallKeyClaimConflict:
        await _record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此安装 Key 已被使用或已过期（并发消费）",
        )

    await _clear_install_key_fail_counter(request.key, request_source)

    logger.info(f"Worker 通过安装 Key 注册成功: {worker.name} ({worker.public_id})")

    return success(
        WorkerRegisterResponse(
            worker_id=worker.public_id,
            api_key=api_key,
            secret_key=secret_key,
        ),
        message="Worker 注册成功",
    )


@router.post(
    "/heartbeat",
    response_model=BaseResponse[dict],
    summary="Worker 心跳",
    description="Worker 定期上报心跳",
)
async def worker_heartbeat(
    request: WorkerHeartbeatRequest,
    auth_info: dict = Depends(verify_worker_request_with_signature),
):
    """Worker 心跳上报（HMAC签名验证）

    认证以 HMAC 签名头(``X-AntCode-Signature`` + ``X-AntCode-Timestamp``)为唯一
    依据,worker_id 从签名校验结果中取出。``WorkerHeartbeatRequest.api_key``
    字段保留为向后兼容,但**不再用于身份校验**——任何凭此字段做认证的逻辑
    都会让明文 API Key 出现在 body / 访问日志里,被视为已弃用。
    """
    # 处理能力上报
    capabilities_dict = None
    if request.capabilities:
        capabilities_dict = request.capabilities.model_dump()

    # 以 HMAC 验签后的 worker_id 为准,忽略 body 里的 worker_id/api_key
    # 防止攻击者用窃取的明文 api_key 伪造心跳,以及防止 body 字段被签名头绕过
    signed_worker_id = (auth_info.get("worker_id") or "").strip()
    if not signed_worker_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少签名认证身份")

    if request.worker_id and request.worker_id != signed_worker_id:
        logger.warning(
            f"心跳 body worker_id 与 HMAC 签名身份不一致, signed={signed_worker_id}, body={request.worker_id}"
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="worker_id 与签名身份不一致")

    # 旁路:如果客户端仍把 api_key 放进 body,记一次弃用告警,但不让它进入日志
    if request.api_key:
        logger.warning(
            f"心跳 body 中携带了弃用的 api_key 字段,worker_id={signed_worker_id};请升级 Worker 端只使用 HMAC 签名头"
        )

    worker = await worker_service.get_worker_by_id(signed_worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="心跳验证失败")

    heartbeat_success = await worker_service.heartbeat(
        worker_id=signed_worker_id,
        status_value=request.status,
        metrics=request.metrics,
        version=request.version,
        # 操作系统信息
        os_type=request.os_type,
        os_version=request.os_version,
        python_version=request.python_version,
        machine_arch=request.machine_arch,
        # Worker 能力
        capabilities=capabilities_dict,
        # 爬虫统计
        spider_stats=request.spider_stats,
    )

    if not heartbeat_success:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="心跳验证失败")

    return success({"success": True}, message="心跳成功")


# ====== 分布式任务分发 API ======


async def _finalize_failed_dispatch_run(run_id: str, reason: str) -> None:
    """P1-FN-07 补偿：把仍处于 pending 的直接分发 run 收敛为 FAILED。

    补偿本身失败只记录（run 会由 reconcile 超时兜底），不掩盖原始异常。
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    try:
        await TaskRun.filter(run_id=run_id, status=TaskStatus.PENDING).update(
            status=TaskStatus.FAILED,
            dispatch_status=DispatchStatus.FAILED,
            end_time=_datetime.now(_UTC),
            error_message=reason[:500],
        )
    except Exception:
        logger.exception("直接分发失败补偿未完成(交由 reconcile 兜底): run_id={}", run_id)


async def _fail_task_dispatch(task_run: TaskRun, run_id: str, error: str | None) -> NoReturn:
    task_run.status = TaskStatus.FAILED
    task_run.dispatch_status = DispatchStatus.FAILED
    task_run.error_message = error or "任务分发失败"
    await task_run.save()
    logger.error("任务分发失败: run_id={} error={}", run_id, error)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="任务分发失败",
    )


async def _get_dispatch_task(project_id: str, task_id_raw, project) -> Task:
    if task_id_raw in (None, "", 0):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "缺少 task_id: 单任务分发必须显式提供真实 task_id, 不接受用 project_id 兜底 (会导致 orphan / 错误归属)"
            ),
        )
    try:
        task_id = int(task_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id 必须是整数") from None

    task = await Task.filter(id=task_id, project_id=project.id).first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"task_id={task_id} 不存在或不属于 project_id={project_id}",
        )
    return task


@router.get(
    "/load/ranking",
    response_model=BaseResponse[list],
    summary="获取 Worker 负载排名",
    description="获取所有在线 Worker 的负载排名，用于任务分发决策",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_workers_load_ranking(
    region: str | None = Query(None, description="区域过滤"),
    top_n: int = Query(10, ge=1, le=50, description="返回前N个 Worker"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取 Worker 负载排名"""
    from antcode_core.application.services.workers import worker_load_balancer

    rankings = await worker_load_balancer.get_workers_ranking(region=region, top_n=top_n)
    return success(rankings)


@router.post(
    "/dispatch/task",
    response_model=BaseResponse[dict],
    summary="分发任务到 Worker",
    description="自动选择最佳 Worker 或指定 Worker 执行任务",
)
async def dispatch_task_to_worker(
    request: WorkerDispatchTaskRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    分发任务到 Worker 执行（支持优先级调度）

    参数:
    - project_id: 项目ID (必须)
    - task_id: 任务ID (必须) —— P1-23 修复: 之前 fallback 到 project.id 会静默
      归属到无关任务(Tortoise 忽略未知字段)或 orphan 执行, 现在强校验;
      如果调用方只想按 project 执行, 应改用带真实 task 的调度入口。
    - params: 执行参数 (可选)
    - environment_vars: 环境变量 (可选)
    - timeout: 超时时间，秒 (默认3600)
    - worker_id: 指定 Worker ID (可选，为空则自动选择)
    - region: 指定区域 (可选)
    - tags: 指定标签 (可选)
    - priority: 优先级 0-4 (可选，0=CRITICAL, 1=HIGH, 2=NORMAL, 3=LOW, 4=IDLE)
    - project_type: 项目类型 (可选，code/file/rule，影响默认优先级)
    - require_render: 是否需要渲染能力 (可选，默认false，用于需要浏览器渲染的爬虫任务)
    """
    import uuid
    from datetime import datetime

    from antcode_core.application.services.projects.project_service import project_service
    from antcode_core.application.services.workers import worker_task_dispatcher
    from antcode_core.domain.models import TaskRun

    project_id = request.get("project_id")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="项目ID不能为空")

    dispatch_worker_id = await _resolve_dispatch_worker(
        request.get("worker_id"),
        current_user,
    )

    # P1-23: 必须显式提供 task_id。之前把 project.id 直接当 task_id 写入 TaskRun
    # 会有两种后果:
    #   * 若数据库里恰好有一条 Task.id == project.id 的记录 → run 静默归到无关任务
    #     (Tortoise 忽略未知字段, 也不会报错)
    #   * 若没有 → orphan run, list_task_runs / task 页面永远查不到, 无法追踪。
    # 修复: 强要求 payload.task_id, 并校验它确实属于当前 project。
    # D1: 按 owner 校验解析项目（admin 放行；非本人 404，防止 IDOR 探测存在性）
    project = await project_service.get_project_by_id(project_id, current_user.user_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    # P1-23: 校验 task 存在且属于该 project, 否则 400。避免跨 project 借用 task_id。
    task_obj = await _get_dispatch_task(project_id, request.get("task_id"), project)

    run_id = str(uuid.uuid4())

    # 创建执行记录（public_id 使用不带横线的 UUID）
    public_id = run_id.replace("-", "")
    task_run = await TaskRun.create(
        run_id=run_id,
        public_id=public_id,
        # P1-23: 使用校验过的真实 task_id, 而不是 project.id
        task_id=task_obj.id,
        project_id=project.id,
        status="pending",
        dispatch_status="pending",
        created_by=current_user.user_id,
        created_at=datetime.now(UTC),
    )

    # P1-FN-07: TaskRun 创建之后的任何校验/分发异常都必须补偿收敛该 run，
    # 否则留下永久 pending 的孤儿执行（此前只有 DispatchResult.success=False
    # 分支会标失败）。
    try:
        # O1-followup: rule 项目派发时把 ProjectRule.to_dispatch_dict() 塞进
        # params.kwargs.rule_detail，让 worker 侧 RulePlugin 能读到规则。
        params = dict(request.get("params") or {})
        project_type = request.get("project_type", "code")
        if project_type == "rule":
            from antcode_core.domain.models.project import ProjectRule

            rule = await ProjectRule.get_or_none(project_id=project.id)
            if rule is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="规则项目缺少 ProjectRule 详情",
                )
            kwargs = dict(params.get("kwargs") or {})
            kwargs["rule_detail"] = rule.to_dispatch_dict()
            params["kwargs"] = kwargs

        environment_vars = dict(request.get("environment_vars") or {})
        environment_vars.pop("ANTCODE_RUNTIME_ENV", None)
        runtime_env_name = None
        if getattr(project, "env_location", None) == "worker" and getattr(project, "worker_env_name", None):
            runtime_env_name = project.worker_env_name

        result = await worker_task_dispatcher.dispatch_task(
            project_id=project_id,
            run_id=run_id,
            params=params,
            environment_vars=environment_vars,
            runtime_env_name=runtime_env_name,
            timeout=request.get("timeout", 3600),
            worker_id=dispatch_worker_id,
            region=request.get("region"),
            tags=request.get("tags"),
            priority=request.get("priority"),
            project_type=project_type,
            require_render=request.get("require_render", False),
        )
    except HTTPException as exc:
        await _finalize_failed_dispatch_run(run_id, f"分发前校验失败: {exc.detail}")
        raise
    except Exception as exc:
        await _finalize_failed_dispatch_run(run_id, f"分发异常: {exc}")
        raise

    if not result.success:
        await _fail_task_dispatch(task_run, run_id, result.error)

    # 更新分发状态
    # P5: DispatchResult.worker_id 是 public_id 字符串，TaskRun.worker_id 是
    # BigIntField（internal FK），历史上直接赋值 → save 抛
    # `invalid literal for int() with base 10: 'worker-xxx'`。解析成 int。
    # 复审 F5-B: 定向 CAS 更新而非整行 save() —— 极快完成的任务终态可能
    # 已先落库，整行回写陈旧对象会把终态 clobber 回 pending。
    dispatch_updates: dict[str, Any] = {"dispatch_status": DispatchStatus.DISPATCHED}
    if result.worker_id:
        from antcode_core.domain.models import Worker as _W

        _worker = await _W.filter(public_id=result.worker_id).only("id").first()
        if _worker:
            dispatch_updates["worker_id"] = _worker.id
    updated = await TaskRun.filter(
        run_id=run_id,
        dispatch_status=DispatchStatus.PENDING,
    ).update(**dispatch_updates)
    if not updated:
        logger.warning("直接分发状态回写被跳过(run 已被推进/取消): run_id={}", run_id)

    from dataclasses import asdict

    return success(asdict(result), message="任务已分发到 Worker")


@router.post(
    "/dispatch/batch",
    response_model=BaseResponse[dict],
    summary="批量分发任务到 Worker",
    description="批量分发多个任务到指定 Worker 的优先级队列",
)
async def dispatch_batch_to_worker(
    request: WorkerDispatchBatchRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    批量分发任务到 Worker 执行

    参数:
    - tasks: 任务列表 (必须)，每个任务包含:
        - task_id: 计划任务数据库 ID
        - run_id: 已存在且属于该任务的运行 ID
        - project_id: 项目ID
        - project_type: 项目类型 (code/file/rule)
        - priority: 优先级 0-4 (可选)
        - params: 执行参数 (可选)
        - environment: 环境变量 (可选)
        - timeout: 超时时间 (可选)
        - require_render: 是否需要渲染能力 (可选)
    - worker_id: 指定 Worker ID (可选，为空则自动选择)
    - region: 指定区域 (可选)
    - tags: 指定标签 (可选)
    - batch_id: 批次ID (可选)
    - require_render: 是否需要渲染能力 (可选，默认false)
    """
    from antcode_core.application.services.projects.project_service import project_service
    from antcode_core.application.services.workers import worker_task_dispatcher

    from antcode_web_api.routes.v1.worker_dispatch_guard import authorize_batch_dispatch_tasks

    dispatch_worker_id = await _resolve_dispatch_worker(
        request.get("worker_id"),
        current_user,
    )
    # P1-FN-03: 授权 + 可重派状态校验(终态/运行中/已取消 run 逐条 409 冲突)。
    tasks = await authorize_batch_dispatch_tasks(
        request.tasks,
        current_user.user_id,
        project_service,
    )

    result = await worker_task_dispatcher.dispatch_batch(
        tasks=tasks,
        worker_id=dispatch_worker_id,
        region=request.get("region"),
        tags=request.get("tags"),
        batch_id=request.get("batch_id"),
        require_render=request.get("require_render", False),
    )

    if not result.success:
        logger.error("批量任务分发失败: batch_id={} error={}", request.get("batch_id"), result.error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="批量任务分发失败",
        )

    from dataclasses import asdict

    return success(asdict(result), message="批量任务已分发到 Worker")


@router.get(
    "/dispatch/queue/{worker_id}/status",
    response_model=BaseResponse[dict],
    summary="获取 Worker 队列状态",
    description="获取指定 Worker 的优先级队列状态",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_worker_queue_status(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取 Worker 队列状态"""
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    status_data = await worker_task_dispatcher.get_queue_status(worker)
    return success(status_data)


@router.put(
    "/dispatch/queue/{worker_id}/tasks/{task_id}/priority",
    response_model=BaseResponse[dict],
    summary="更新任务优先级",
    description="更新 Worker 队列中任务的优先级",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def update_worker_task_priority(
    worker_id: str,
    task_id: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user),
):
    """更新 Worker 队列中任务的优先级"""
    from antcode_core.application.services.workers import worker_task_dispatcher

    priority = request.get("priority")
    if priority is None or not (0 <= priority <= 4):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="优先级必须是 0-4 之间的整数",
        )

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    result = await worker_task_dispatcher.update_task_priority(worker, task_id, priority)

    if not result.get("success"):
        error = result.get("error", "")
        if error == "当前架构暂不支持该操作":
            raise HTTPException(status_code=501, detail=error)
        if "不存在" in error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="队列任务不存在")
        logger.error(
            "更新 Worker 队列任务优先级失败: worker_id={} task_id={} error={}",
            worker_id,
            task_id,
            error,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新优先级失败",
        )

    return success(result, message="优先级已更新")


@router.delete(
    "/dispatch/queue/{worker_id}/tasks/{task_id}",
    response_model=BaseResponse[dict],
    summary="取消队列中的任务",
    description="取消 Worker 优先级队列中的任务",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def cancel_worker_queued_task(worker_id: str, task_id: str, current_user: TokenData = Depends(get_current_user)):
    """取消 Worker 队列中的任务"""
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    success_flag = await worker_task_dispatcher.cancel_queued_task(worker, task_id)

    if not success_flag:
        raise HTTPException(status_code=501, detail="当前架构暂不支持该操作")

    return success({"task_id": task_id}, message="任务已取消")


@router.get(
    "/dispatch/task/{worker_id}/{task_id}/status",
    response_model=BaseResponse[dict],
    summary="获取分布式任务状态",
    description="从指定 Worker 获取任务执行状态",
)
async def get_distributed_task_status(
    worker_id: str, task_id: str, current_user: TokenData = Depends(get_current_user)
):
    """从 Worker 获取任务状态"""
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await _require_worker_access(worker_id, current_user)
    await _require_run_access(task_id, current_user)

    status_data = await worker_task_dispatcher.get_task_status_from_worker(worker, task_id)

    if not status_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在或无法获取状态")

    return success(status_data)


@router.get(
    "/dispatch/task/{worker_id}/{task_id}/logs",
    response_model=BaseResponse[dict],
    summary="获取分布式任务日志",
    description="从指定 Worker 获取任务执行日志",
)
async def get_distributed_task_logs(
    worker_id: str,
    task_id: str,
    log_type: str = Query("output", description="日志类型: output/error"),
    tail: int = Query(100, ge=1, le=1000, description="返回最后N行"),
    current_user: TokenData = Depends(get_current_user),
):
    """从 Worker 获取任务日志"""
    from antcode_core.application.services.workers import worker_task_dispatcher

    worker = await _require_worker_access(worker_id, current_user)
    await _require_run_access(task_id, current_user)

    logs = await worker_task_dispatcher.get_task_logs_from_worker(worker, task_id, log_type, tail)

    return success({"logs": logs, "total": len(logs), "worker_id": worker_id, "task_id": task_id})


@router.get(
    "/best",
    response_model=BaseResponse[dict],
    summary="获取最佳 Worker",
    description="根据负载自动选择最适合执行任务的 Worker",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_best_worker(
    region: str | None = Query(None, description="区域过滤"),
    tags: str | None = Query(None, description="标签过滤，逗号分隔"),
    require_render: bool = Query(False, description="是否需要渲染能力"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取当前负载最低的最佳 Worker"""
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
            "worker": _worker_to_response(best_worker).model_dump(),
            "load_score": score,
        }
    )


@router.get(
    "/render-capable",
    response_model=BaseResponse[WorkerListResponse],
    summary="获取有渲染能力的 Worker",
    description="获取所有具有浏览器渲染能力（DrissionPage）的在线 Worker",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_render_capable_workers(
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    region: str | None = Query(None, description="区域过滤"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取有渲染能力的 Worker 列表"""
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
    items = [_worker_to_response(worker) for worker in workers]

    return success(WorkerListResponse(items=items, total=total, page=page, size=size))


# ====== Worker 上报接口（Worker 调用，无需用户认证）======


@router.post(
    "/report-log",
    response_model=BaseResponse[dict],
    summary="上报任务日志",
    description="Worker 实时上报任务执行日志",
)
async def report_task_log(
    request: WorkerTaskLogReportRequest = Body(...),
    auth_context: dict = Depends(_verify_worker_credential_headers),
):
    """任务日志上报（签名 + Worker 标识 + API Key）"""
    from antcode_core.application.services.logs.postgres_log_service import TaskRunGoneError
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_run,
    )

    await require_worker_owns_run(auth_context["worker"], request.run_id)

    # 存储日志
    try:
        await distributed_log_service.append_log(
            request.run_id,
            request.log_type,
            request.content,
        )
    except TaskRunGoneError as exc:
        # P1-DB-03: run 已被删除（与删除路径的 advisory lock 串行化后在锁内
        # 校验命中），明确 409 拒绝而不是 5xx 引导 Worker 无限重试。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务执行已删除，日志被拒绝") from exc

    return success({"received": True})


@router.post(
    "/report-logs-batch",
    response_model=BaseResponse[dict],
    summary="批量上报任务日志",
    description="Worker 批量上报任务执行日志",
)
async def report_task_logs_batch(
    request: WorkerTaskLogsBatchReportRequest = Body(...),
    auth_context: dict = Depends(_verify_worker_credential_headers),
):
    """批量任务日志上报（签名 + Worker 标识 + API Key）"""
    from antcode_core.application.services.logs.postgres_log_service import TaskRunGoneError
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_runs,
    )

    logs = request.logs
    await require_worker_owns_runs(auth_context["worker"], {item.run_id for item in logs})

    grouped_logs: dict[tuple[str, str], list[str]] = {}
    for item in logs:
        key = (item.run_id, item.log_type)
        grouped_logs.setdefault(key, []).append(item.content)

    semaphore = asyncio.Semaphore(16)

    async def _append_group(run_id: str, log_type: str, contents: list[str]) -> int:
        async with semaphore:
            await distributed_log_service.append_logs(run_id, log_type, contents)
        return len(contents)

    group_keys = list(grouped_logs.keys())
    results = await asyncio.gather(
        *(_append_group(run_id, log_type, contents) for (run_id, log_type), contents in grouped_logs.items()),
        return_exceptions=True,
    )
    failed_pairs = [(group_keys[i], result) for i, result in enumerate(results) if isinstance(result, Exception)]
    if failed_pairs:
        logger.error("批量日志写入失败: failed_groups={} total_groups={}", len(failed_pairs), len(results))
        # P1-DB-03: 全部失败均为"run 已删除"时返回 409（永久拒绝，重试无意义）；
        # 其余情况原返回 503,让 Worker 整批重试。P1-round6 5.2 收紧: 返回
        # partial-success 结构告知调用方哪些 (run_id, log_type) 组失败, Worker
        # 只重试失败组;已成功组不会因整批重试复制入库(distributed_log_service
        # 用递增 sequence,重复 append 会写重复行)。
        failed_exceptions = [exc for _, exc in failed_pairs]
        if all(isinstance(exc, TaskRunGoneError) for exc in failed_exceptions):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务执行已删除，日志被拒绝")
        received_count = sum(result for result in results if isinstance(result, int))
        failed_groups = [{"run_id": rid, "log_type": lt, "error": str(exc)} for (rid, lt), exc in failed_pairs]
        raise HTTPException(
            status_code=status.HTTP_207_MULTI_STATUS,
            detail={
                "message": "批量日志部分失败, 请只重试失败组",
                "received": received_count,
                "total": len(logs),
                "failed_groups": failed_groups,
            },
        )
    received_count = sum(result for result in results if isinstance(result, int))

    return success({"received": received_count, "total": len(logs)})


@router.post(
    "/report-heartbeat",
    response_model=BaseResponse[dict],
    summary="上报任务执行心跳",
    description="Worker 上报任务执行心跳，用于检测任务中断",
)
async def report_execution_heartbeat(
    request: WorkerTaskHeartbeatReportRequest = Body(...),
    auth_context: dict = Depends(_verify_worker_credential_headers),
):
    """任务执行心跳上报"""
    from antcode_core.application.services.scheduler.task_persistence import task_persistence_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_run,
    )

    await require_worker_owns_run(auth_context["worker"], request.run_id)

    success_flag = await task_persistence_service.update_heartbeat(request.run_id)
    return success({"updated": success_flag})


@router.post(
    "/report-task",
    response_model=BaseResponse[dict],
    summary="上报任务状态",
    description="Worker 上报任务执行状态",
)
async def report_task_status(
    request: WorkerTaskStatusReportRequest = Body(...),
    auth_context: dict = Depends(_verify_worker_credential_headers),
):
    """任务状态上报（签名 + Worker 标识 + API Key）"""
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
    from antcode_core.application.services.workers.run_ownership_service import (
        require_worker_owns_run,
    )

    await require_worker_owns_run(auth_context["worker"], request.run_id)

    # 更新任务状态
    await distributed_log_service.update_task_status(
        request.run_id,
        request.status,
        exit_code=request.exit_code,
        error_message=request.error_message,
    )

    return success({"updated": True})


@router.get(
    "/distributed-logs/{run_id}",
    response_model=BaseResponse[dict],
    summary="获取分布式任务日志",
    description="获取在远程 Worker 执行的任务日志",
)
async def get_distributed_logs(
    run_id: str,
    log_type: str = Query("stdout", description="日志类型: stdout/stderr"),
    tail: int = Query(100, ge=1, le=5000, description="返回最后N行"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取分布式任务的日志"""
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service

    await _require_run_access(run_id, current_user)

    logs = await distributed_log_service.get_logs(run_id, log_type=log_type, tail=tail)

    return success(
        {
            "run_id": run_id,
            "log_type": log_type,
            "logs": logs,
            "total": len(logs),
        }
    )


# ====== Worker 资源管理 API（管理员功能）======


@router.get(
    "/{worker_id}/resources",
    response_model=BaseResponse[dict],
    summary="获取 Worker 资源限制",
    description="获取指定 Worker 的资源限制和监控状态（需要管理员权限）",
)
async def get_worker_resources(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """
    获取 Worker 资源限制（管理员可查看）

    返回:
    - limits: 当前资源限制配置
    - auto_adjustment: 是否启用自适应调整
    - resource_stats: 实时资源统计
    """
    from antcode_core.common.config import settings
    from antcode_core.domain.models import User

    # 检查管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限查看资源配置")

    # 获取 Worker 信息
    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    # 基于心跳/数据库指标返回资源信息
    resources = worker.metrics if isinstance(worker.metrics, dict) else {}
    limits = worker.resource_limits if isinstance(worker.resource_limits, dict) else {}

    def _to_float(value: object, default: float = 0.0) -> float:
        if not isinstance(value, (str, int, float)):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    max_concurrent_default = settings.MAX_CONCURRENT_TASKS
    memory_default = settings.TASK_MEMORY_LIMIT_MB
    cpu_default = settings.TASK_CPU_TIME_LIMIT_SEC

    return success(
        {
            "limits": {
                "max_concurrent_tasks": limits.get("max_concurrent_tasks", max_concurrent_default),
                "task_memory_limit_mb": limits.get("task_memory_limit_mb", memory_default),
                "task_cpu_time_limit_sec": limits.get("task_cpu_time_limit_sec", cpu_default),
            },
            "auto_adjustment": limits.get("auto_resource_limit", True),
            "resource_stats": {
                "cpu_percent": round(_to_float(resources.get("cpu", resources.get("cpu_percent", 0))), 1),
                "memory_percent": round(_to_float(resources.get("memory", resources.get("memory_percent", 0))), 1),
                "disk_percent": round(_to_float(resources.get("disk", resources.get("disk_percent", 0))), 1),
                "memory_used_mb": resources.get("memoryUsed", resources.get("memory_used_mb", 0)),
                "memory_total_mb": resources.get("memoryTotal", resources.get("memory_total_mb", 0)),
                "disk_used_gb": resources.get("diskUsed", resources.get("disk_used_gb", 0)),
                "disk_total_gb": resources.get("diskTotal", resources.get("disk_total_gb", 0)),
                "running_tasks": resources.get("runningTasks", resources.get("running_tasks", 0)),
                "queued_tasks": resources.get("queuedTasks", resources.get("queued_tasks", 0)),
                "uptime_seconds": resources.get("uptime", resources.get("uptime_seconds", 0)),
            },
        }
    )


@router.post(
    "/{worker_id}/resources",
    response_model=BaseResponse[dict],
    summary="调整 Worker 资源限制",
    description="手动调整指定 Worker 的资源限制（仅超级管理员）",
)
async def update_worker_resources(
    worker_id: str,
    request: dict = Body(...),
    current_user: TokenData = Depends(get_current_user),
):
    """
    调整 Worker 资源限制（仅 admin 用户可修改）

    参数:
    - max_concurrent_tasks: 最大并发任务数 (1-20)
    - task_memory_limit_mb: 单任务内存限制 (256-8192 MB)
    - task_cpu_time_limit_sec: 单任务CPU时间限制 (60-3600 秒)
    - auto_resource_limit: 是否启用自适应资源限制
    """
    from antcode_core.domain.models import User
    from antcode_core.infrastructure.redis import get_redis_client

    # 检查超级管理员权限
    user = await User.get_or_none(id=current_user.user_id)
    if not user or not user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要超级管理员权限修改资源配置",
        )

    # 获取 Worker 信息
    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    # 参数验证
    max_concurrent = request.get("max_concurrent_tasks")
    memory_limit = request.get("task_memory_limit_mb")
    cpu_limit = request.get("task_cpu_time_limit_sec")
    auto_limit = request.get("auto_resource_limit")

    # 范围校验
    if max_concurrent is not None and not (1 <= max_concurrent <= 20):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="最大并发任务数必须在 1-20 之间",
        )
    if memory_limit is not None and not (256 <= memory_limit <= 8192):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="单任务内存限制必须在 256-8192 MB 之间",
        )
    if cpu_limit is not None and not (60 <= cpu_limit <= 3600):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="单任务CPU时间限制必须在 60-3600 秒之间",
        )

    # 构建配置参数
    config_params = {}
    if max_concurrent is not None:
        config_params["max_concurrent_tasks"] = str(max_concurrent)
    if memory_limit is not None:
        config_params["task_memory_limit_mb"] = str(memory_limit)
    if cpu_limit is not None:
        config_params["task_cpu_time_limit_sec"] = str(cpu_limit)
    if auto_limit is not None:
        config_params["auto_resource_limit"] = str(auto_limit).lower()

    if not config_params:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要提供一个配置项")

    # 更新数据库中的资源限制配置
    if worker.resource_limits is None:
        worker.resource_limits = {}
    if max_concurrent is not None:
        worker.resource_limits["max_concurrent_tasks"] = max_concurrent
    if memory_limit is not None:
        worker.resource_limits["task_memory_limit_mb"] = memory_limit
    if cpu_limit is not None:
        worker.resource_limits["task_cpu_time_limit_sec"] = cpu_limit
    if auto_limit is not None:
        worker.resource_limits["auto_resource_limit"] = auto_limit
    await worker.save()

    # 通过 Redis 控制通道发送配置更新
    synced = False
    try:
        from antcode_core.application.services.runtime.runtime_control_service import (
            write_control_event,
        )

        redis = await get_redis_client()
        payload = build_config_update_control_payload(config_params)
        # P2-24: 走 write_control_event 带 maxlen 近似裁剪,
        # 避免 control:{worker_id} stream 无限增长。
        await write_control_event(redis, control_stream(worker.public_id), payload)
        synced = True
    except Exception as e:
        logger.warning(f"发送配置更新失败: {e}")

    logger.info(f"超级管理员 {user.username} 调整了 Worker {worker_id} 的资源限制: {config_params}")

    return success(
        {"updated": config_params, "synced": synced},
        message="资源限制已更新",
    )


# ====== 爬虫统计 API ======


@router.get(
    "/stats/spider",
    response_model=BaseResponse[dict],
    summary="获取集群爬虫统计",
    description="获取所有在线 Worker 的爬虫统计聚合数据",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_cluster_spider_stats(current_user: TokenData = Depends(get_current_user)):
    """获取集群爬虫统计"""
    from antcode_core.application.services.workers.spider_stats_service import spider_stats_service

    stats = await spider_stats_service.get_cluster_spider_stats()
    return success(stats)


@router.get(
    "/{worker_id}/stats/spider",
    response_model=BaseResponse[dict],
    summary="获取单 Worker 爬虫统计",
    description="获取指定 Worker 的爬虫统计数据",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_worker_spider_stats(worker_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取单 Worker 爬虫统计"""
    from antcode_core.application.services.workers.spider_stats_service import spider_stats_service

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    stats = await spider_stats_service.get_worker_spider_stats(worker.id)
    return success(stats.model_dump())


@router.get(
    "/{worker_id}/stats/spider/history",
    response_model=BaseResponse[list],
    summary="获取 Worker 爬虫统计历史",
    description="获取指定 Worker 的爬虫统计历史趋势数据",
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
async def get_worker_spider_stats_history(
    worker_id: str,
    hours: int = Query(1, ge=1, le=24, description="查询时间范围（小时）"),
    current_user: TokenData = Depends(get_current_user),
):
    """获取 Worker 爬虫统计历史"""
    from antcode_core.application.services.workers.spider_stats_service import spider_stats_service

    worker = await worker_service.get_worker_by_id(worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")

    history = await spider_stats_service.get_spider_stats_history(worker_id=worker.id, hours=hours)
    return success(history)


promote_static_routes(router, {"/best", "/render-capable"})

workers_router = router

__all__ = ["workers_router", "router"]
