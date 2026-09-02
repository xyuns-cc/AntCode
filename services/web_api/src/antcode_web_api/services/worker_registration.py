"""Recoverable V2 Worker install-key registration."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from antcode_core.common.config import settings
from antcode_core.common.security.api_key import hash_api_key, store_api_key, store_secret_key
from antcode_core.common.security.worker_registration import (
    CREDENTIAL_DERIVATION_VERSION,
    DerivedWorkerCredentials,
    derive_worker_credentials,
    hash_recovery_secret,
)
from antcode_core.domain.models import Worker, WorkerInstallKey, WorkerStatus
from antcode_core.domain.schemas.worker import WorkerRegisterByKeyV2Request
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from antcode_web_api.services.worker_registration_validation import (
    as_utc,
    digest_matches,
    registration_request_hash,
    source_matches,
)


class RegistrationError(RuntimeError):
    """Base V2 registration failure."""


class RegistrationExpired(RegistrationError):
    pass


class RegistrationConflict(RegistrationError):
    pass


class RegistrationForbidden(RegistrationError):
    pass


class RegistrationNotFound(RegistrationError):
    pass


RegistrationLeaseEnabler = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class RegistrationContext:
    request: WorkerRegisterByKeyV2Request
    request_source: str
    allowed_source: str
    recovery_secret: str
    recovery_secret_hash: str
    request_hash: str


@dataclass(frozen=True)
class RegistrationResult:
    worker_id: str
    api_key: str
    secret_key: str
    registration_id: str
    recovery_expires_at: datetime
    recovered: bool


async def register_or_recover(
    request: WorkerRegisterByKeyV2Request,
    install_key: WorkerInstallKey,
    request_source: str,
) -> RegistrationResult:
    context = _registration_context(request, install_key, request_source)
    if install_key.status == "used":
        return await _recover_registration(context, install_key)
    if install_key.status != "pending" or not _pending_key_unexpired(install_key):
        raise RegistrationExpired("安装 Key 已过期")
    try:
        return await _create_registration(context, install_key)
    except RegistrationConflict:
        refreshed = await WorkerInstallKey.get_or_none(key=install_key.key)
        if refreshed is None or refreshed.status != "used":
            raise
        return await _recover_registration(context, refreshed)
    except IntegrityError as exc:
        raise RegistrationConflict("Worker 名称或 registration_id 已存在") from exc


async def acknowledge_registration(
    worker_id: str,
    registration_id: str,
    *,
    lease_enabler: RegistrationLeaseEnabler,
) -> datetime:
    install_key = await WorkerInstallKey.get_or_none(registration_id=registration_id)
    if install_key is None:
        raise RegistrationNotFound("注册确认记录不存在")
    if install_key.status != "used":
        raise RegistrationConflict("注册恢复窗口已关闭")
    if install_key.used_by_worker != worker_id:
        raise RegistrationForbidden("注册确认身份不匹配")
    if install_key.registration_acknowledged_at is not None:
        acknowledged_at = as_utc(install_key.registration_acknowledged_at)
        await lease_enabler(worker_id)
        return acknowledged_at
    acknowledged_at = datetime.now(UTC)
    updated = await WorkerInstallKey.filter(
        registration_id=registration_id,
        used_by_worker=worker_id,
        status="used",
        registration_acknowledged_at__isnull=True,
    ).update(
        registration_acknowledged_at=acknowledged_at,
        recovery_secret_hash=None,
        recovery_expires_at=None,
    )
    if updated == 1:
        await lease_enabler(worker_id)
        return acknowledged_at
    refreshed = await WorkerInstallKey.get_or_none(registration_id=registration_id)
    if refreshed is None or refreshed.registration_acknowledged_at is None:
        raise RegistrationConflict("注册确认并发更新失败")
    acknowledged_at = as_utc(refreshed.registration_acknowledged_at)
    await lease_enabler(worker_id)
    return acknowledged_at


async def _create_registration(
    context: RegistrationContext,
    install_key: WorkerInstallKey,
) -> RegistrationResult:
    credentials = _derive_credentials(context, install_key)
    now = datetime.now(UTC)
    recovery_expires_at = now + timedelta(seconds=settings.WORKER_REGISTRATION_RECOVERY_SECONDS)
    placeholder_id = f"pending:{secrets.token_hex(8)}"
    async with in_transaction("default") as connection:
        updated = (
            await WorkerInstallKey.filter(
                key=install_key.key,
                status="pending",
                expires_at__gt=now,
            )
            .using_db(connection)
            .update(
                status="used",
                used_by_worker=placeholder_id,
                used_at=now,
                allowed_source=context.allowed_source,
                registration_id=context.request.registration_id,
                recovery_secret_hash=context.recovery_secret_hash,
                registration_request_hash=context.request_hash,
                credential_derivation_version=CREDENTIAL_DERIVATION_VERSION,
                recovery_expires_at=recovery_expires_at,
            )
        )
        if updated != 1:
            raise RegistrationConflict("安装 Key 已被并发消费")
        worker = _new_worker(context, install_key, credentials)
        await worker.save(using_db=connection)
        finalized = (
            await WorkerInstallKey.filter(
                key=install_key.key,
                registration_id=context.request.registration_id,
                used_by_worker=placeholder_id,
            )
            .using_db(connection)
            .update(used_by_worker=worker.public_id)
        )
        if finalized != 1:
            raise RegistrationConflict("安装 Key Worker 身份回写失败")
    return _result(
        context,
        worker.public_id,
        credentials,
        recovery_expires_at=recovery_expires_at,
        recovered=False,
    )


async def _recover_registration(
    context: RegistrationContext,
    install_key: WorkerInstallKey,
) -> RegistrationResult:
    _validate_recovery(context, install_key)
    worker = await Worker.get_or_none(public_id=install_key.used_by_worker)
    if worker is None:
        raise RegistrationConflict("安装 Key 对应 Worker 不存在")
    credentials = _derive_credentials(context, install_key)
    if not _worker_credentials_match(worker, credentials):
        raise RegistrationConflict("恢复凭据与 Worker 当前身份不一致")
    # 恢复注册就是重新引导：Worker 进程刚起来、还没有 Lease，也就还没有心跳。
    # Lease 资格白名单是 {connecting, online}，若把它留在 offline，它永远拿不到
    # 首个 Lease。回到 connecting 同时刷新 updated_at，为心跳监控的引导窗口
    # （worker_liveness.is_within_bootstrap_window）提供锚点。
    if worker.status != WorkerStatus.CONNECTING.value:
        worker.status = WorkerStatus.CONNECTING.value
        await worker.save(update_fields=["status", "updated_at"])
    recovery_expires_at = as_utc(install_key.recovery_expires_at)
    return _result(
        context,
        worker.public_id,
        credentials,
        recovery_expires_at=recovery_expires_at,
        recovered=True,
    )


def _validate_recovery(context: RegistrationContext, install_key: WorkerInstallKey) -> None:
    if install_key.registration_acknowledged_at is not None:
        raise RegistrationConflict("Worker 注册已确认，恢复窗口已关闭")
    if install_key.registration_id != context.request.registration_id:
        raise RegistrationConflict("registration_id 与已消费安装 Key 不匹配")
    if not digest_matches(install_key.recovery_secret_hash, context.recovery_secret_hash):
        raise RegistrationForbidden("恢复秘密无效")
    if not digest_matches(install_key.registration_request_hash, context.request_hash):
        raise RegistrationConflict("恢复请求与首次注册参数不一致")
    if install_key.credential_derivation_version != CREDENTIAL_DERIVATION_VERSION:
        raise RegistrationConflict("凭据派生版本不匹配")
    if not source_matches(context.request_source, install_key.allowed_source or ""):
        raise RegistrationForbidden("恢复请求来源不匹配")
    if install_key.recovery_expires_at is None or datetime.now(UTC) >= as_utc(install_key.recovery_expires_at):
        raise RegistrationExpired("Worker 注册恢复窗口已过期")


def _registration_context(
    request: WorkerRegisterByKeyV2Request,
    install_key: WorkerInstallKey,
    request_source: str,
) -> RegistrationContext:
    recovery_secret = request.recovery_secret.get_secret_value()
    return RegistrationContext(
        request=request,
        request_source=request_source,
        allowed_source=install_key.allowed_source or request_source,
        recovery_secret=recovery_secret,
        recovery_secret_hash=hash_recovery_secret(recovery_secret),
        request_hash=registration_request_hash(request),
    )


def _derive_credentials(
    context: RegistrationContext,
    install_key: WorkerInstallKey,
) -> DerivedWorkerCredentials:
    return derive_worker_credentials(install_key.key, context.request.registration_id, context.recovery_secret)


def _new_worker(
    context: RegistrationContext,
    install_key: WorkerInstallKey,
    credentials: DerivedWorkerCredentials,
) -> Worker:
    request = context.request
    worker = Worker(
        name=request.name,
        host=request.host,
        port=request.port,
        region=request.region,
        status="connecting",
        created_by=install_key.created_by,
        transport_mode=request.transport_mode,
    )
    store_api_key(worker, credentials.api_key)
    store_secret_key(worker, credentials.secret_key)
    return worker


def _result(
    context: RegistrationContext,
    worker_id: str,
    credentials: DerivedWorkerCredentials,
    *,
    recovery_expires_at: datetime,
    recovered: bool,
) -> RegistrationResult:
    return RegistrationResult(
        worker_id=worker_id,
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        registration_id=context.request.registration_id,
        recovery_expires_at=recovery_expires_at,
        recovered=recovered,
    )


def _worker_credentials_match(worker: Worker, credentials: DerivedWorkerCredentials) -> bool:
    return digest_matches(worker.api_key_hash, hash_api_key(credentials.api_key)) and digest_matches(
        worker.secret_key_hash,
        hash_api_key(credentials.secret_key),
    )


def _pending_key_unexpired(install_key: WorkerInstallKey) -> bool:
    return datetime.now(UTC) < as_utc(install_key.expires_at)
