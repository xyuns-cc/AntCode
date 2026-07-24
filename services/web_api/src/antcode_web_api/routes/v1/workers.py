"""Worker 管理 API"""

import json
import os
import sys as _sys
import time
from ipaddress import ip_address, ip_network

from antcode_core.application.services.workers import worker_service
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import store_api_key, store_secret_key
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.common.security.network_source import extract_client_ip
from antcode_core.common.security.worker_auth import (
    verify_worker_request_with_signature,
)
from antcode_core.domain.models import (
    Task,
    TaskRun,
    User,
    Worker,
    WorkerInstallKey,
)
from antcode_core.domain.schemas.worker import (  # noqa: F401
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
from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from tortoise.expressions import Q

from antcode_web_api.response import BaseResponse, success

# P2 拆分: 10 个 CRUD handler 移到 workers_crud.py。
from antcode_web_api.routes.v1 import workers_crud as _workers_crud

# P2 拆分: 5 个 dispatch handler + 3 schema + 4 helper 移至 workers_dispatch.py。
# 顶层 re-export schema/常量 让测试引用继续可命中。
from antcode_web_api.routes.v1 import workers_dispatch as _workers_dispatch

# P2 拆分: 3 个 distributed logs / status 查询接口移到 workers_distributed.py。
from antcode_web_api.routes.v1 import workers_distributed as _workers_distributed

# P2 拆分: 5 个权限管理 handler (my/available, users, assign, revoke, batch-assign)
# 移到 workers_permission.py。
from antcode_web_api.routes.v1 import workers_permission as _workers_permission

# P2 拆分: 3 个纯查询接口 (load ranking / best worker / render-capable) 移到
# workers_query.py。主 workers.py 底部调 register_query_routes 挂路由 +
# 顶层保留 3 个 shim 让 workers_route.get_best_worker(...) 测试引用不变。
from antcode_web_api.routes.v1 import workers_query as _workers_query

# P2 拆分: register-direct / register(410 shim) / heartbeat 移到 workers_register.py。
# install_key 相关 (generate_install_key / register_worker_by_key + 5 helper)
# 仍留主文件, 因依赖链耦合深。
from antcode_web_api.routes.v1 import workers_register as _workers_register

# P2 拆分: 2 个资源管理 handler (get/update resources) 移到 workers_resources.py。
from antcode_web_api.routes.v1 import workers_resources as _workers_resources

# P2 拆分: 3 个 spider stats 查询接口移到 workers_spider.py。
from antcode_web_api.routes.v1 import workers_spider as _workers_spider

# P2 拆分: 3 个 stats/history 查询接口移到 workers_stats.py。
from antcode_web_api.routes.v1 import workers_stats as _workers_stats
from antcode_web_api.routes.v1.workers_dispatch import (  # noqa: F401
    WorkerDispatchBatchRequest,
    WorkerDispatchBatchTaskRequest,
    WorkerDispatchTaskRequest,
)

# P2 拆分: Worker 上报接口的 schema + handler + 常量集中在 workers_report.py,
# 主 workers.py 只保留 re-export 与底部 register_report_routes 注册, 让
# 测试的 workers_route.WorkerTaskLogReportRequest / workers_route.report_task_log
# 等引用继续可命中。noqa: F401 显式保留 re-export 防 ruff 误删。
from antcode_web_api.routes.v1.workers_report import (  # noqa: F401
    MAX_LOG_BATCH_ENTRIES,
    MAX_LOG_LINE_CHARS,
    WorkerTaskHeartbeatReportRequest,
    WorkerTaskLogReportRequest,
    WorkerTaskLogsBatchReportRequest,
    WorkerTaskStatusReportRequest,
    _WorkerReportBaseModel,
    register_report_routes,
    report_execution_heartbeat,
    report_task_log,
    report_task_logs_batch,
    report_task_status,
)
from antcode_web_api.routing import promote_static_routes
from antcode_web_api.services.worker_installer import (
    WorkerInstallCommandRequest,
    WorkerInstallerConfigurationError,
    build_worker_install_command,
    load_worker_install_config,
)

router = APIRouter()


# P2 拆分: dispatch schema + 常量已移至 workers_dispatch.py, 顶部已 re-export。


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


# P2 拆分: _resolve_dispatch_worker 已移至 workers_dispatch.py, 但顶层保留 shim
# 让测试 `monkeypatch.setattr(workers, "_resolve_dispatch_worker", ...)` 可命中,
# dispatch handler 通过传参使用 workers._resolve_dispatch_worker 而不是模块内
# 部函数, 保证 monkeypatch 生效。
async def _resolve_dispatch_worker(
    requested_worker_id: str | None,
    current_user: TokenData,
) -> str | None:
    return await _workers_dispatch._resolve_dispatch_worker(
        requested_worker_id,
        current_user,
        require_worker_access=_require_worker_access,
        request_user=_request_user,
    )


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


# P2 拆分: get_worker_stats / get_cluster_metrics_history / get_worker_metrics_history
# 移至 workers_stats.py, 通过 register_stats_routes 挂路由; 顶层 shim 保留原名。
async def get_worker_stats(current_user=None):
    _ = current_user
    return await _workers_stats.get_worker_stats()


async def get_cluster_metrics_history(hours: int = 24, current_user=None):
    _ = current_user
    return await _workers_stats.get_cluster_metrics_history(hours)


# P2 拆分: 10 个 CRUD handler 移至 workers_crud.py, 通过 register_crud_routes
# 挂 @router; 顶层 shim 保留原名让测试引用继续可命中。
async def get_workers(*, page=1, size=20, status_filter=None, region=None, search=None, current_user=None):
    return await _workers_crud.get_workers(
        page=page,
        size=size,
        status_filter=status_filter,
        region=region,
        search=search,
        current_user=current_user,
        list_accessible_workers=_list_accessible_workers,
        worker_to_response=_worker_to_response,
    )


async def create_worker(request, http_request, current_user):
    return await _workers_crud.create_worker(
        request, http_request, current_user, worker_to_response=_worker_to_response
    )


async def get_worker_credentials(worker_id, current_user):
    return await _workers_crud.get_worker_credentials(
        worker_id, current_user, require_worker_access=_require_worker_access
    )


async def disconnect_worker(worker_id, current_user=None):
    _ = current_user
    return await _workers_crud.disconnect_worker(worker_id)


async def get_worker(worker_id, current_user):
    return await _workers_crud.get_worker(
        worker_id,
        current_user,
        require_worker_access=_require_worker_access,
        worker_to_response=_worker_to_response,
    )


async def update_worker(worker_id, request, current_user=None):
    _ = current_user
    return await _workers_crud.update_worker(worker_id, request, worker_to_response=_worker_to_response)


async def delete_worker(worker_id, http_request, current_user):
    return await _workers_crud.delete_worker(worker_id, http_request, current_user)


async def batch_delete_workers(request, current_user=None):
    _ = current_user
    return await _workers_crud.batch_delete_workers(request)


async def test_worker_connection(worker_id, current_user=None):
    _ = current_user
    return await _workers_crud.test_worker_connection(worker_id)


async def refresh_worker_status(worker_id, current_user=None):
    _ = current_user
    return await _workers_crud.refresh_worker_status(worker_id, worker_to_response=_worker_to_response)


# ====== Worker 权限管理 API（需要管理员权限）======


# P2 拆分: 5 个权限管理 handler 移至 workers_permission.py, 通过
# register_permission_routes 挂 @router; 顶层 shim 保留原名让测试引用可继续。
async def get_my_available_workers(current_user):
    return await _workers_permission.get_my_available_workers(current_user, _worker_to_response)


async def get_worker_users(worker_id: str, current_user):
    return await _workers_permission.get_worker_users(worker_id, current_user)


async def assign_worker_permission(worker_id: str, request: dict, current_user):
    return await _workers_permission.assign_worker_permission(worker_id, request, current_user)


async def revoke_worker_permission(worker_id: str, user_id: str, current_user):
    return await _workers_permission.revoke_worker_permission(worker_id, user_id, current_user)


async def batch_assign_workers(request: dict, current_user):
    return await _workers_permission.batch_assign_workers(request, current_user)


# ====== Worker 端调用的 API（无需用户认证）======


async def get_worker_metrics_history(worker_id: str, hours: int = 24, current_user=None):
    return await _workers_stats.get_worker_metrics_history(
        worker_id, hours, current_user, require_worker_access=_require_worker_access
    )


# P2 拆分: register_direct_worker 移至 workers_register.py; 顶层 shim 保留原名。
async def register_direct_worker(request):
    return await _workers_register.register_direct_worker(request, mask_redis_url=_mask_redis_url)


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


# P2 拆分: register_worker (410 shim) 移至 workers_register.py
async def register_worker(request):
    return await _workers_register.register_worker(request)


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


# P2 拆分: worker_heartbeat 移至 workers_register.py
async def worker_heartbeat(request, auth_info):
    return await _workers_register.worker_heartbeat(request, auth_info)


# P2 拆分: 3 query shim (load_ranking / best_worker / render_capable) 已移至
# workers_query.py, 顶层保留 shim 让 workers_route.get_best_worker(...) 测试可用。
async def get_workers_load_ranking(*, region=None, top_n=10, current_user=None):
    _ = current_user
    return await _workers_query.get_workers_load_ranking(region=region, top_n=top_n)


async def get_best_worker(*, region=None, tags=None, require_render=False, current_user=None):
    _ = current_user
    return await _workers_query.get_best_worker(
        region=region,
        tags=tags,
        require_render=require_render,
        worker_to_response=_worker_to_response,
    )


async def get_render_capable_workers(*, page=1, size=20, region=None, current_user=None):
    _ = current_user
    return await _workers_query.get_render_capable_workers(
        page=page,
        size=size,
        region=region,
        worker_to_response=_worker_to_response,
    )


# P2 拆分: dispatch/batch/queue/priority/cancel_queued 5 handler + 3 schema + 4 helper
# 移至 workers_dispatch.py。顶层 shim 保留 workers_route.dispatch_task_to_worker 等
# 测试引用。register_dispatch_routes 在文件末尾挂路由。
async def dispatch_task_to_worker(request, current_user):
    import sys

    return await _workers_dispatch.dispatch_task_to_worker(request, current_user, workers_module=sys.modules[__name__])


async def dispatch_batch_to_worker(request, current_user):
    import sys

    return await _workers_dispatch.dispatch_batch_to_worker(request, current_user, workers_module=sys.modules[__name__])


async def get_worker_queue_status(worker_id, current_user=None):
    _ = current_user
    return await _workers_dispatch.get_worker_queue_status(worker_id)


async def update_worker_task_priority(worker_id, task_id, request, current_user=None):
    _ = current_user
    return await _workers_dispatch.update_worker_task_priority(worker_id, task_id, request)


async def cancel_worker_queued_task(worker_id, task_id, current_user=None):
    _ = current_user
    return await _workers_dispatch.cancel_worker_queued_task(worker_id, task_id)


# P2 拆分: distributed task status/logs 3 个 handler 移至 workers_distributed.py,
# 通过 register_distributed_routes 挂 @router 装饰(见文件末尾)。
# workers_route.get_distributed_task_status 等测试引用通过下面 shim 保留。
async def get_distributed_task_status(worker_id: str, task_id: str, current_user):
    return await _workers_distributed.get_distributed_task_status(
        worker_id,
        task_id,
        current_user,
        require_worker_access=_require_worker_access,
        require_run_access=_require_run_access,
    )


async def get_distributed_task_logs(
    worker_id: str, task_id: str, log_type: str = "output", tail: int = 100, current_user=None
):
    return await _workers_distributed.get_distributed_task_logs(
        worker_id,
        task_id,
        log_type,
        tail=tail,
        current_user=current_user,
        require_worker_access=_require_worker_access,
        require_run_access=_require_run_access,
    )


# P2 拆分: 4 个 Worker 上报接口注册委托给 workers_report.register_report_routes,
# 传入 _verify_worker_credential_headers 依赖, 保持 URL / 契约不变。
register_report_routes(router, _verify_worker_credential_headers)

# P2 拆分: 3 个查询接口通过 workers_query.register_query_routes 挂路由, 保持
# URL (/load/ranking, /best, /render-capable) 与 DI 不变。
_workers_query.register_query_routes(router, _worker_to_response)


async def get_distributed_logs(run_id: str, log_type: str = "stdout", tail: int = 100, current_user=None):
    return await _workers_distributed.get_distributed_logs(
        run_id, log_type, tail=tail, current_user=current_user, require_run_access=_require_run_access
    )


# P2 拆分: get/update resources 2 handler 移至 workers_resources.py, 通过
# register_resources_routes 挂 @router; 顶层 shim 保留原名。
async def get_worker_resources(worker_id: str, current_user):
    return await _workers_resources.get_worker_resources(worker_id, current_user)


async def update_worker_resources(worker_id: str, request: dict, current_user):
    return await _workers_resources.update_worker_resources(worker_id, request, current_user)


# P2 拆分: 3 个 spider stats 查询接口移至 workers_spider.py, 通过
# register_spider_routes 挂 @router 装饰; workers_route.get_cluster_spider_stats
# 等测试引用通过下面的 shim 保留。
async def get_cluster_spider_stats(current_user=None):
    _ = current_user
    return await _workers_spider.get_cluster_spider_stats()


async def get_worker_spider_stats(worker_id: str, current_user=None):
    _ = current_user
    return await _workers_spider.get_worker_spider_stats(worker_id)


async def get_worker_spider_stats_history(worker_id: str, hours: int = 1, current_user=None):
    _ = current_user
    return await _workers_spider.get_worker_spider_stats_history(worker_id, hours)


_workers_spider.register_spider_routes(router)
# P2 拆分: distributed logs / status 3 handler 挂路由
_workers_distributed.register_distributed_routes(router, _require_worker_access, _require_run_access)
# P2 拆分: 5 个权限管理 handler 挂路由 (my/available, users, assign, revoke, batch-assign)
_workers_permission.register_permission_routes(router, _worker_to_response)
# P2 拆分: 2 个资源管理 handler 挂路由 (GET/POST /workers/{id}/resources)
_workers_resources.register_resources_routes(router)
# P2 拆分: 5 个 dispatch handler 挂路由 (task/batch/queue/priority/cancel_queued)
_workers_dispatch.register_dispatch_routes(router, _sys.modules[__name__])
# P2 拆分: 3 个 stats/history 查询 handler 挂路由
_workers_stats.register_stats_routes(router, _require_worker_access)
# P2 拆分: register-direct / register(410) / heartbeat 3 handler 挂路由
_workers_register.register_register_routes(router, _mask_redis_url)
# P2 拆分: 10 个 CRUD handler 挂路由
_workers_crud.register_crud_routes(
    router,
    worker_to_response=_worker_to_response,
    require_worker_access=_require_worker_access,
    list_accessible_workers=_list_accessible_workers,
)

promote_static_routes(router, {"/best", "/render-capable"})

workers_router = router

__all__ = ["workers_router", "router"]
