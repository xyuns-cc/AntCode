"""Administrator lifecycle operations for Worker install keys."""

from __future__ import annotations

import re
from ipaddress import ip_address, ip_network
from typing import Any, Literal, cast

from antcode_core.common.config import settings
from antcode_core.common.security.auth import TokenData
from antcode_core.domain.models import User, WorkerInstallKey
from antcode_core.domain.schemas.worker import (
    WorkerInstallKeyListItem,
    WorkerInstallKeyRequest,
    WorkerInstallKeyResponse,
)
from fastapi import HTTPException, Request, status
from loguru import logger

from antcode_web_api.response import page, success
from antcode_web_api.services.worker_installer import (
    WorkerInstallCommandRequest,
    WorkerInstallerConfigurationError,
    build_worker_install_command,
)

INSTALL_KEY_PUBLIC_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


async def generate_install_key(
    request: WorkerInstallKeyRequest,
    http_request: Request,
    current_user: TokenData,
    *,
    workers_module: Any,
):
    """Validate distribution first, then persist a one-time V2 install key."""
    _ = http_request
    await _require_admin(current_user)
    os_type = _validated_os_type(request.os_type)
    allowed_source = _validated_allowed_source(request.allowed_source)
    install_config = _load_install_config(workers_module)
    plaintext_key = WorkerInstallKey.generate_key()
    install_command = build_worker_install_command(
        WorkerInstallCommandRequest(os_type=os_type, install_key=plaintext_key),
        install_config,
    )
    install_key = await WorkerInstallKey.persist_install_key(
        plaintext_key,
        os_type=os_type,
        created_by=current_user.user_id,
        allowed_source=allowed_source,
    )
    response = WorkerInstallKeyResponse(
        key=plaintext_key,
        os_type=os_type,
        allowed_source=allowed_source,
        install_command=install_command,
        expires_at=install_key.expires_at,
    )
    return success(response, message="安装命令已生成，请复制到目标机器执行")


async def list_install_keys(page_number: int, page_size: int, current_user: TokenData):
    await _require_admin(current_user)
    query = WorkerInstallKey.all()
    total = await query.count()
    keys = await query.order_by("-created_at").offset((page_number - 1) * page_size).limit(page_size)
    return page([_list_item(key) for key in keys], total, page_number, page_size)


async def revoke_install_key(key_id: str, current_user: TokenData):
    await _require_admin(current_user)
    if not INSTALL_KEY_PUBLIC_ID_PATTERN.fullmatch(key_id):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="安装 Key ID 格式无效")
    current_status = await WorkerInstallKey.revoke_pending(key_id)
    if current_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="安装 Key 不存在")
    if current_status != "revoked":
        detail = f"安装 Key 状态为 {current_status}，不能撤销"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return success({"id": key_id, "status": current_status}, message="安装 Key 已撤销")


async def _require_admin(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if user is None or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def _validated_allowed_source(raw: str | None) -> str | None:
    """校验来源绑定必须是 IP 或 CIDR,否则在生成时就拒绝。

    兑换侧 ``_is_source_match`` 只认 ``ip_address`` / ``ip_network``,任何主机名
    或畸形值都会恒定不匹配。若在这里放行,管理员会拿到一枚**永远无法兑换**的
    安装 Key,而失败只在 Worker 注册时表现为「来源不匹配」,极难定位。空值表示
    不预先绑定——此时仍有 ``_claim_install_key_source_once`` 的首次使用即绑定
    (TOFU) 兜底,Key 不会被第二个来源复用。
    """
    value = (raw or "").strip()
    if not value:
        return None
    try:
        if "/" in value:
            ip_network(value, strict=False)
        else:
            ip_address(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="来源绑定必须是 IP 地址或 CIDR 网段（如 10.0.0.5 或 10.0.0.0/24）",
        ) from exc
    return value


def _validated_os_type(os_type: str) -> str:
    normalized = os_type.lower()
    if normalized not in ("linux", "macos", "windows"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="操作系统类型必须是 linux、macos 或 windows",
        )
    return normalized


def _load_install_config(workers_module: Any):
    try:
        return workers_module.load_worker_install_config(settings)
    except WorkerInstallerConfigurationError as exc:
        logger.exception("Worker 安装分发配置无效")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Worker 安装分发未正确配置",
        ) from exc


def _list_item(key: WorkerInstallKey) -> WorkerInstallKeyListItem:
    key_status = cast(Literal["pending", "used", "expired", "revoked"], key.status)
    return WorkerInstallKeyListItem(
        id=key.public_id,
        os_type=key.os_type,
        status=key_status,
        allowed_source=key.allowed_source,
        used_by_worker=key.used_by_worker,
        created_at=key.created_at,
        expires_at=key.expires_at,
        used_at=key.used_at,
        registration_acknowledged_at=key.registration_acknowledged_at,
    )


__all__ = ["generate_install_key", "list_install_keys", "revoke_install_key"]
