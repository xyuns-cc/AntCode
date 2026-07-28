"""Worker installer and recoverable registration endpoints."""

from typing import Any

from antcode_core.common.security.worker_auth import verify_worker_request_with_signature
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.worker import (
    WorkerRegisterByKeyV2Request,
    WorkerRegisterByKeyV2Response,
    WorkerRegistrationAckRequest,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger

from antcode_web_api.response import success
from antcode_web_api.services.worker_installer import install_script_sha256, read_install_script
from antcode_web_api.services.worker_registration import (
    RegistrationConflict,
    RegistrationExpired,
    RegistrationForbidden,
    RegistrationNotFound,
    acknowledge_registration,
    register_or_recover,
)

router = APIRouter()


def _script_response(script_name: str, media_type: str) -> Response:
    digest = install_script_sha256(script_name)
    return Response(
        content=read_install_script(script_name),
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=300",
            "ETag": f'"{digest}"',
            "X-Content-SHA256": digest,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/install.sh", summary="下载 Linux/macOS Worker 安装脚本")
def download_unix_worker_installer() -> Response:
    return _script_response("install_worker.sh", "text/x-shellscript")


@router.get("/install.ps1", summary="下载 Windows Worker 安装脚本")
def download_windows_worker_installer() -> Response:
    return _script_response("install_worker.ps1", "text/plain")


@router.post(
    "/register-by-key-v2",
    response_model=BaseResponse[WorkerRegisterByKeyV2Response],
    summary="使用安装 Key 可恢复注册 Worker",
)
async def register_worker_by_key_v2(
    request: WorkerRegisterByKeyV2Request,
    http_request: Request,
):
    workers_route = _workers_route()
    request_source = workers_route._extract_request_source(http_request)
    install_key = await _validate_install_key(request, request_source, workers_route)
    claim_ok, claim_message = await workers_route._claim_install_key_source_once(
        request.key,
        request_source,
        request.client_timestamp,
        request.client_nonce,
    )
    if not claim_ok:
        await workers_route._record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=claim_message)
    try:
        result = await register_or_recover(request, install_key, request_source)
    except (RegistrationConflict, RegistrationExpired, RegistrationForbidden) as exc:
        await workers_route._record_install_key_failed_attempt(request.key, request_source)
        raise _registration_http_error(exc) from exc
    await _clear_fail_counter_best_effort(request.key, request_source, workers_route)
    return success(WorkerRegisterByKeyV2Response(**result.__dict__), message="Worker 注册成功")


async def _registration_ack_auth(
    http_request: Request,
    auth_info: dict = Depends(verify_worker_request_with_signature),
) -> dict:
    return await _workers_route()._verify_worker_credential_headers(http_request, auth_info)


@router.post(
    "/{worker_id}/registration-ack",
    response_model=BaseResponse[dict],
    summary="确认 Worker 注册凭据已耐久保存",
)
async def acknowledge_worker_registration(
    worker_id: str,
    request: WorkerRegistrationAckRequest,
    auth_context: dict = Depends(_registration_ack_auth),
):
    worker = auth_context["worker"]
    if worker.public_id != worker_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker 身份与路径不匹配")
    try:
        acknowledged_at = await acknowledge_registration(worker_id, request.registration_id)
    except RegistrationNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RegistrationForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except RegistrationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success(
        {"registration_id": request.registration_id, "acknowledged_at": acknowledged_at.isoformat()},
        message="Worker 注册已确认",
    )


async def _validate_install_key(
    request: WorkerRegisterByKeyV2Request,
    request_source: str,
    workers_route: Any,
):
    from antcode_core.domain.models import WorkerInstallKey

    blocked, block_ttl = await workers_route._check_install_key_blocked(request.key, request_source)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"注册尝试过于频繁，请 {block_ttl} 秒后重试",
        )
    install_key = await WorkerInstallKey.find_by_plaintext(request.key)
    if install_key is None or not WorkerInstallKey.matches_plaintext(install_key.key, request.key):
        await workers_route._record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="安装 Key 不存在")
    allowed_source = (install_key.allowed_source or "").strip()
    if allowed_source and not workers_route._is_source_match(request_source, allowed_source):
        await workers_route._record_install_key_failed_attempt(request.key, request_source)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="来源不在安装 Key 允许范围内")
    return install_key


async def _clear_fail_counter_best_effort(key: str, source: str, workers_route: Any) -> None:
    try:
        await workers_route._clear_install_key_fail_counter(key, source)
    except Exception:
        logger.exception("Worker V2 注册成功后清理失败计数器失败")


def _registration_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RegistrationForbidden):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, RegistrationExpired):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _workers_route():
    from antcode_web_api.routes.v1 import workers

    return workers


__all__ = ["router"]
