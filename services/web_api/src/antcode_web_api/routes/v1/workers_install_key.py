"""Worker install-key 管理与 Redis ACL 签发接口。

P2 拆分自 workers.py:
- POST /workers/generate-install-key (generate_install_key)
- GET /workers/install-keys (list_install_keys)
- DELETE /workers/install-keys/{key_id} (revoke_install_key)
- POST /workers/{worker_id}/redis-acl/issue (issue_worker_redis_acl)

install_key 判定链 (source 提取 / 失败计数 / 来源封禁 / 事务消费) helper
一律通过 `_workers_module()` 延迟解析 workers 主模块, 让单测的
`monkeypatch.setattr(workers_route, "get_redis_client", ...)` 及
`monkeypatch.setattr(workers_route, "_check_install_key_blocked", ...)` 等
补丁仍然生效。workers.py 通过顶层 `from .workers_install_key import ...`
re-export 保持 `workers_route.generate_install_key` /
`workers_route._extract_request_source` 等公开名。

`_verify_worker_credential_headers` 依赖仍留在 workers.py 主文件 (供
report handler 与本模块 redis-acl 签发接口共用), 通过
`register_install_key_routes(..., verify_worker_credential_headers=...)`
注入避免循环 import。
"""

from __future__ import annotations

import os
import time
from ipaddress import ip_address, ip_network

from antcode_core.common.config import settings
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.common.security.network_source import extract_client_ip
from antcode_core.domain.models import WorkerInstallKey
from antcode_core.domain.schemas.worker import (
    WorkerInstallKeyRequest,
    WorkerInstallKeyResponse,
)
from antcode_core.infrastructure.redis import (
    install_key_redis_digest,
    worker_install_key_block_key,
    worker_install_key_claim_key,
    worker_install_key_fail_counter_key,
    worker_install_key_nonce_key,
    worker_install_source_block_key,
    worker_install_source_fail_counter_key,
)
from fastapi import Depends, HTTPException, Request, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.routes.v1 import workers_install_key_admin

INSTALL_KEY_DEFAULT_PAGE_SIZE = 20
INSTALL_KEY_MAX_PAGE_SIZE = 100


def _workers_module():
    """延迟解析 workers 主模块。

    handler / helper 通过 `_workers_module().get_redis_client()`、
    `_workers_module()._check_install_key_blocked(...)` 等方式访问, 保证
    tests 在 workers_route 上 monkeypatch 的最新绑定生效 (若直接使用本模块
    自身的名称, 会捕获 import 时的原始函数, 补丁失效)。
    """
    from antcode_web_api.routes.v1 import workers as _workers

    return _workers


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
    redis = await _workers_module().get_redis_client()
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
    redis = await _workers_module().get_redis_client()
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
    redis = await _workers_module().get_redis_client()
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
    redis = await _workers_module().get_redis_client()
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


async def _is_registration_acknowledged(worker_id: str) -> bool:
    return await WorkerInstallKey.filter(
        status="used",
        used_by_worker=worker_id,
        registration_acknowledged_at__isnull=False,
    ).exists()


async def generate_install_key(
    request: WorkerInstallKeyRequest,
    http_request: Request,
    current_user: TokenData,
):
    return await workers_install_key_admin.generate_install_key(
        request,
        http_request,
        current_user,
        workers_module=_workers_module(),
    )


async def list_install_keys(page_number: int, page_size: int, current_user: TokenData):
    return await workers_install_key_admin.list_install_keys(page_number, page_size, current_user)


async def revoke_install_key(key_id: str, current_user: TokenData):
    return await workers_install_key_admin.revoke_install_key(key_id, current_user)


async def issue_worker_redis_acl(worker_id: str, auth_context: dict):
    """Rotate Redis credentials for the signed, path-bound Direct Worker."""
    from antcode_core.common.config import settings
    from antcode_core.common.security.redis_acl import ensure_worker_acl

    _workers = _workers_module()

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
    if not await _workers._is_registration_acknowledged(worker.public_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Worker V2 注册尚未完成 ACK，拒绝签发 Redis ACL",
        )
    # delay import 让测试 monkeypatch antcode_core.infrastructure.redis.get_redis_client 生效
    from antcode_core.infrastructure.redis import get_redis_client

    redis = await get_redis_client()
    if redis is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis ACL 服务不可用")
    password = await ensure_worker_acl(redis, worker)
    return success(
        {"redis_username": worker.redis_username, "redis_password": password},
        message="Redis ACL 签发成功",
    )


def register_install_key_routes(router, verify_worker_credential_headers) -> None:
    """挂载 install_key 管理与 ACL 签发接口。"""

    @router.post(
        "/{worker_id}/redis-acl/issue",
        response_model=BaseResponse[dict],
        summary="签发 Direct Worker Redis ACL",
    )
    async def _issue_worker_redis_acl(
        worker_id: str,
        auth_context: dict = Depends(verify_worker_credential_headers),
    ):
        return await issue_worker_redis_acl(worker_id, auth_context)

    @router.post(
        "/generate-install-key",
        response_model=BaseResponse[WorkerInstallKeyResponse],
        summary="生成 Worker 安装 Key",
        description="生成一次性安装 Key，用于 Worker 快速注册（管理员）",
    )
    async def _generate_install_key(
        request: WorkerInstallKeyRequest,
        http_request: Request,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await generate_install_key(request, http_request, current_user)

    @router.get("/install-keys")
    async def _list_install_keys(
        page_number: int = 1,
        page_size: int = INSTALL_KEY_DEFAULT_PAGE_SIZE,
        current_user: TokenData = Depends(get_current_user),
    ):
        if page_number < 1 or page_size < 1 or page_size > INSTALL_KEY_MAX_PAGE_SIZE:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="分页参数无效")
        return await list_install_keys(page_number, page_size, current_user)

    @router.delete("/install-keys/{key_id}")
    async def _revoke_install_key(
        key_id: str,
        current_user: TokenData = Depends(get_current_user),
    ):
        return await revoke_install_key(key_id, current_user)


__all__ = [
    "WorkerInstallKeyRequest",
    "WorkerInstallKeyResponse",
    "_check_install_key_blocked",
    "_claim_install_key_source_once",
    "_clear_install_key_fail_counter",
    "_extract_request_source",
    "_is_registration_acknowledged",
    "_is_source_match",
    "_record_install_key_failed_attempt",
    "generate_install_key",
    "issue_worker_redis_acl",
    "list_install_keys",
    "register_install_key_routes",
    "revoke_install_key",
]
