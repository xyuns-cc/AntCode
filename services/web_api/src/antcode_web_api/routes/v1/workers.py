"""Worker 管理 API"""

import sys as _sys

from antcode_core.application.services.run_ownership import resolve_run_owner_id
from antcode_core.application.services.workers import worker_service
from antcode_core.common.config import settings  # noqa: F401  # 供测试 monkeypatch workers_route.settings 使用
from antcode_core.common.security.api_key import store_api_key, store_secret_key  # noqa: F401
from antcode_core.common.security.auth import TokenData
from antcode_core.common.security.worker_auth import (
    verify_worker_request_with_signature,
)
from antcode_core.domain.models import (  # noqa: F401  # Task 供测试 monkeypatch workers_route.Task 使用
    Task,
    TaskRun,
    User,
    Worker,
)
from antcode_core.domain.schemas.worker import (  # noqa: F401
    WorkerCapabilities,
    WorkerCreateRequest,
    WorkerHeartbeatRequest,
    WorkerListResponse,
    WorkerMetrics,
    WorkerRegisterDirectRequest,
    WorkerRegisterDirectResponse,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
    WorkerResponse,
    WorkerTestConnectionResponse,
    WorkerUpdateRequest,
)

# get_redis_client 顶层 re-export: 部分单元测试通过
# `monkeypatch.setattr(workers_route, "get_redis_client", ...)` 补丁 workers 模块属性,
# install_key handler / helper 经延迟 `_workers_module().get_redis_client(...)` 命中此绑定。
from antcode_core.infrastructure.redis import get_redis_client  # noqa: F401
from fastapi import APIRouter, Depends, HTTPException, Request, status
from tortoise.expressions import Q

# P2 拆分: 10 个 CRUD handler 移到 workers_crud.py。
from antcode_web_api.routes.v1 import workers_crud as _workers_crud
from antcode_web_api.routes.v1 import workers_direct_control_routes as _workers_direct_control_routes

# P2 拆分: 5 个 dispatch handler + 3 schema + 4 helper 移至 workers_dispatch.py。
# 顶层 re-export schema/常量 让测试引用继续可命中。
from antcode_web_api.routes.v1 import workers_dispatch as _workers_dispatch

# P2 拆分: 3 个 distributed logs / status 查询接口移到 workers_distributed.py。
from antcode_web_api.routes.v1 import workers_distributed as _workers_distributed

# P2 拆分: install_key 相关 3 handler + 8 helper + 1 exception 移到
# workers_install_key.py; 顶层通过 re-export 保留 workers_route.X 兼容。
from antcode_web_api.routes.v1 import workers_install_key as _workers_install_key

# P2 拆分: 5 个权限管理 handler (my/available, users, assign, revoke, batch-assign)
# 移到 workers_permission.py。
from antcode_web_api.routes.v1 import workers_permission as _workers_permission

# P2 拆分: 3 个纯查询接口 (load ranking / best worker / render-capable) 移到
# workers_query.py。主 workers.py 底部调 register_query_routes 挂路由 +
# 顶层保留 3 个 shim 让 workers_route.get_best_worker(...) 测试引用不变。
from antcode_web_api.routes.v1 import workers_query as _workers_query

# P2 拆分: register-direct / register(410 shim) / heartbeat 移到 workers_register.py。
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
from antcode_web_api.routes.v1.workers_install_key import (  # noqa: F401
    WorkerInstallKeyRequest,
    WorkerInstallKeyResponse,
    _check_install_key_blocked,
    _claim_install_key_source_once,
    _clear_install_key_fail_counter,
    _extract_request_source,
    _is_registration_acknowledged,
    _is_source_match,
    _record_install_key_failed_attempt,
)

# P2 拆分: Worker 上报接口的 schema + handler + 常量集中在 workers_report.py, 主
# workers.py 只保留 re-export 与底部 register_report_routes 注册, 让测试的
# workers_route.report_task_log 等引用继续可命中 (noqa: F401 防 ruff 误删)。
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

# load_worker_install_config 顶层 re-export: 单元测试通过
# `monkeypatch.setattr(workers_route, "load_worker_install_config", ...)` 补丁
# workers 模块属性, generate_install_key 在 workers_install_key.py 里通过
# `_workers_module().load_worker_install_config(...)` 命中此绑定。
from antcode_web_api.services.worker_installer import (  # noqa: F401
    load_worker_install_config,
)

router = APIRouter()


# P2 拆分: dispatch schema + 常量已移至 workers_dispatch.py, 顶部已 re-export。
# install_key 相关 8 helper + 1 exception 移至 workers_install_key.py, 顶部通过
# re-export 保留 workers_route._extract_request_source 等旧名的兼容引用。


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
    # 所有者按 run 类型解析：批次 run 没有 Task 行，只查 Task 会锁死批次所有者。
    user = await _request_user(current_user)
    if user.is_admin:
        return
    execution = await TaskRun.filter(Q(run_id=run_id) | Q(public_id=run_id)).first()
    owner_id = await resolve_run_owner_id(execution) if execution else None
    if owner_id is None or owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")


# P2 拆分: _resolve_dispatch_worker 已移至 workers_dispatch.py, 顶层保留 shim 让测试
# `monkeypatch.setattr(workers, "_resolve_dispatch_worker", ...)` 可命中: dispatch handler 通过传参使用它而非模块内部函数。
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
    # 这两列由 Worker 侧写、由控制面 schema 读回，两端各自发布，键集迟早会错配。
    # 吞掉 ValidationError 换默认值不是"少显示几个字段"：整份 metrics 会塌成
    # cpu=0 / maxConcurrentTasks=5，与一台真正空闲的 Worker 逐字节相同，页面、日志、
    # 告警都分辨不出来。真机上三台 Worker 就这样显示了整整一个版本的 CPU 0%。
    # 让 ValidationError 原样冒泡，错配当场可见并带上缺的键名。
    metrics = WorkerMetrics(**worker.metrics) if worker.metrics else WorkerMetrics()
    capabilities = WorkerCapabilities(**worker.capabilities) if worker.capabilities else WorkerCapabilities()

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


async def update_worker(worker_id, request, *, http_request, current_user):
    return await _workers_crud.update_worker(
        worker_id,
        request,
        http_request=http_request,
        current_user=current_user,
        worker_to_response=_worker_to_response,
    )


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


async def get_my_available_workers(current_user):
    return await _workers_permission.get_my_available_workers(current_user, _worker_to_response)


async def get_worker_users(worker_id: str, current_user):
    return await _workers_permission.get_worker_users(worker_id, current_user)


async def assign_worker_permission(
    worker_id: str, request: _workers_permission.WorkerPermissionAssignRequest, current_user
):
    return await _workers_permission.assign_worker_permission(worker_id, request, current_user)


async def revoke_worker_permission(worker_id: str, user_id: str, current_user):
    return await _workers_permission.revoke_worker_permission(worker_id, user_id, current_user)


async def batch_assign_workers(request: _workers_permission.WorkerPermissionBatchAssignRequest, current_user):
    return await _workers_permission.batch_assign_workers(request, current_user)


# ====== Worker 端调用的 API（无需用户认证）======


async def get_worker_metrics_history(worker_id: str, hours: int = 24, current_user=None):
    return await _workers_stats.get_worker_metrics_history(
        worker_id, hours, current_user, require_worker_access=_require_worker_access
    )


# P2 拆分: register_direct_worker 移至 workers_register.py; 顶层 shim 保留原名。
async def register_direct_worker(request):
    return await _workers_register.register_direct_worker(request, mask_redis_url=_mask_redis_url)


# P2 拆分: issue_worker_redis_acl 移至 workers_install_key.py, 顶层 shim 保留原名。
async def issue_worker_redis_acl(worker_id: str, auth_context: dict):
    return await _workers_install_key.issue_worker_redis_acl(worker_id, auth_context)


# P2 拆分: register_worker (410 shim) 移至 workers_register.py
async def register_worker(request):
    return await _workers_register.register_worker(request)


# P2 拆分: generate_install_key 移至 workers_install_key.py。
async def generate_install_key(request, http_request, current_user):
    return await _workers_install_key.generate_install_key(request, http_request, current_user)


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


async def update_worker_resources(worker_id: str, request: dict, current_user, *, http_request):
    return await _workers_resources.update_worker_resources(worker_id, request, current_user, http_request=http_request)


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
_workers_crud.register_crud_routes(
    router,
    worker_to_response=_worker_to_response,
    require_worker_access=_require_worker_access,
    list_accessible_workers=_list_accessible_workers,
)
_workers_install_key.register_install_key_routes(router, _verify_worker_credential_headers)
_workers_direct_control_routes.register_direct_control_routes(router, _verify_worker_credential_headers)

promote_static_routes(router, {"/best", "/render-capable"})

workers_router = router

__all__ = ["workers_router", "router"]
