"""Worker install-key 相关接口 (generate / register-by-key / redis-acl issue)。

P2 拆分自 workers.py: 3 个 handler + 8 个 helper + 1 个 exception:
- POST /workers/generate-install-key (generate_install_key)
- POST /workers/register-by-key (register_worker_by_key)
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

import json
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
    WorkerRegisterByKeyRequest,
    WorkerRegisterResponse,
)
from antcode_core.infrastructure.redis import (
    install_key_redis_digest,
    worker_install_key_block_key,
    worker_install_key_claim_key,
    worker_install_key_fail_counter_key,
    worker_install_key_meta_key,
    worker_install_key_nonce_key,
    worker_install_source_block_key,
    worker_install_source_fail_counter_key,
)
from fastapi import Depends, HTTPException, Request, status
from loguru import logger

from antcode_web_api.response import BaseResponse, success
from antcode_web_api.services.worker_installer import (
    WorkerInstallCommandRequest,
    WorkerInstallerConfigurationError,
    build_worker_install_command,
)


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
        # 走 workers namespace lookup 让测试 patch_object(workers_route, "store_api_key") 命中
        _workers = _workers_module()
        _workers.store_api_key(worker, api_key)
        _workers.store_secret_key(worker, secret_key)
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
    """生成 Worker 安装 Key

    生成一次性安装命令，复制到目标机器执行即可完成 Worker 注册。
    类似 nezha 探针的工作模式。
    """
    from antcode_core.domain.models import User, WorkerInstallKey

    _workers = _workers_module()

    _ = http_request

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
        install_config = _workers.load_worker_install_config(settings)
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

    redis = await _workers.get_redis_client()
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


async def register_worker_by_key(
    request: WorkerRegisterByKeyRequest,
    http_request: Request,
):
    """Worker 使用安装 Key 注册

    Worker 启动时通过环境变量获取 Key，调用此接口完成注册。
    """
    from antcode_core.domain.models import WorkerInstallKey

    _workers = _workers_module()

    if not request.client_nonce or len(request.client_nonce.strip()) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少有效的 client_nonce")

    request_source = _workers._extract_request_source(http_request)

    is_blocked, block_ttl = await _workers._check_install_key_blocked(request.key, request_source)
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
        await _workers._record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="安装 Key 不存在")

    if not install_key.is_valid():
        await _workers._record_install_key_failed_attempt(request.key, request_source)
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
    if allowed_source and not _workers._is_source_match(request_source, allowed_source):
        await _workers._record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="来源不在安装 Key 允许范围内",
        )

    claim_ok, claim_message = await _workers._claim_install_key_source_once(
        key=request.key,
        source=request_source,
        request_timestamp=request.client_timestamp,
        request_nonce=request.client_nonce,
    )
    if not claim_ok:
        await _workers._record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=claim_message)

    try:
        worker, api_key, secret_key = await _workers._create_worker_from_install_key(
            request,
            install_key,
            request_source,
        )
    except _workers._InstallKeyClaimConflict:
        await _workers._record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此安装 Key 已被使用或已过期（并发消费）",
        )

    await _workers._clear_install_key_fail_counter(request.key, request_source)

    logger.info(f"Worker 通过安装 Key 注册成功: {worker.name} ({worker.public_id})")

    return success(
        WorkerRegisterResponse(
            worker_id=worker.public_id,
            api_key=api_key,
            secret_key=secret_key,
        ),
        message="Worker 注册成功",
    )


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
    """挂载 install_key 3 handler; verify_worker_credential_headers 由 workers.py 注入。"""

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

    @router.post(
        "/register-by-key",
        response_model=BaseResponse[WorkerRegisterResponse],
        summary="使用 Key 注册 Worker",
        description="Worker 使用安装 Key 进行注册（无需认证）",
    )
    async def _register_worker_by_key(request: WorkerRegisterByKeyRequest, http_request: Request):
        return await register_worker_by_key(request, http_request)


__all__ = [
    "WorkerInstallKeyRequest",
    "WorkerInstallKeyResponse",
    "WorkerRegisterByKeyRequest",
    "WorkerRegisterResponse",
    "_InstallKeyClaimConflict",
    "_check_install_key_blocked",
    "_claim_install_key_source_once",
    "_clear_install_key_fail_counter",
    "_create_worker_from_install_key",
    "_extract_request_source",
    "_is_registration_acknowledged",
    "_is_source_match",
    "_record_install_key_failed_attempt",
    "generate_install_key",
    "issue_worker_redis_acl",
    "register_install_key_routes",
    "register_worker_by_key",
]
