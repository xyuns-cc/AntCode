"""项目下载 API。"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from loguru import logger
from pydantic import RootModel, field_validator

from antcode_core.application.services.files.file_storage import file_storage_service
from antcode_core.application.services.projects.managed_paths import (
    is_managed_path,
    resolve_managed_path,
)
from antcode_core.application.services.projects.project_sync_service import project_sync_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.common.security.worker_auth import worker_auth_verifier
from antcode_core.domain.models import Project, User, Worker
from antcode_core.domain.schemas.common import BaseResponse
from antcode_web_api.response import success as success_response

router = APIRouter()
_MAX_INCREMENTAL_FILE_COUNT = 2000
_MAX_RELATIVE_PATH_LEN = 512
_HASH_PATTERN = re.compile(r"^[A-Fa-f0-9]{32,128}$")
_S3_TRANSFER_PREFIX = "s3_"
_MANAGED_ARCHIVE_METHOD = "managed_archive"


class IncrementalSyncHashes(RootModel[dict[str, str]]):
    """客户端增量文件哈希映射。"""

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: dict[str, str]) -> dict[str, str]:
        try:
            return _validate_client_file_hashes(value)
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc


async def _ensure_project_access(project_id: str, current_user: TokenData) -> Project:
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    user = await User.get_or_none(id=current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或会话已失效")

    if not user.is_admin and project.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问")

    return project


def _validate_client_file_hashes(client_file_hashes: dict[str, Any]) -> dict[str, str]:
    if not isinstance(client_file_hashes, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="client_file_hashes 必须是对象")
    if len(client_file_hashes) > _MAX_INCREMENTAL_FILE_COUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"增量文件数量过多，最多 {_MAX_INCREMENTAL_FILE_COUNT} 条",
        )

    normalized: dict[str, str] = {}
    for raw_path, raw_hash in client_file_hashes.items():
        if not isinstance(raw_path, str) or not isinstance(raw_hash, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件路径和哈希必须是字符串")

        path = raw_path.strip()
        file_hash = raw_hash.strip().lower()
        if not path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件路径不能为空")
        if len(path) > _MAX_RELATIVE_PATH_LEN:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件路径过长")
        if path.startswith("/") or "\\" in path or "\x00" in path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件路径格式非法")

        segments = [segment for segment in path.split("/") if segment]
        if not segments or any(segment == ".." for segment in segments):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件路径包含非法目录")
        if not _HASH_PATTERN.fullmatch(file_hash):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件哈希格式非法")
        normalized[path] = file_hash

    return normalized


@router.get("/{project_id}/transfer-info", response_model=BaseResponse[dict[str, Any]])
async def get_project_transfer_info(
    project_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    project = await _ensure_project_access(project_id, current_user)
    transfer_info = await project_sync_service.get_project_transfer_info(project.id, project=project)
    return success_response(transfer_info)


@router.get("/{project_id}/download", response_model=None)
async def download_project_file(
    project_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    project = await _ensure_project_access(project_id, current_user)
    transfer_info = await project_sync_service.get_project_transfer_info(project.id, project=project)
    logger.info(
        f"下载 [{project.name}] {transfer_info.get('transfer_method', '')} "
        f"{transfer_info.get('file_size', 0)}字节"
    )
    return await _download_transfer(transfer_info)


@router.post("/{project_id}/incremental-sync", response_model=BaseResponse[dict[str, Any]])
async def get_incremental_changes(
    project_id: str,
    client_file_hashes: IncrementalSyncHashes = Body(...),
    current_user: TokenData = Depends(get_current_user),
):
    project = await _ensure_project_access(project_id, current_user)
    changes = await project_sync_service.get_incremental_changes(project.id, client_file_hashes.root)
    return success_response(changes)


async def verify_worker_download_request(project_id: str, request: Request) -> Worker:
    worker_id = (request.query_params.get("worker_id") or "").strip()
    timestamp_str = (request.query_params.get("timestamp") or "").strip()
    nonce = (request.query_params.get("nonce") or "").strip()
    signature = (request.query_params.get("signature") or "").strip()

    if not all([worker_id, timestamp_str, nonce, signature]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少签名参数")

    try:
        timestamp = int(timestamp_str)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="时间戳格式错误") from exc

    worker = await Worker.get_or_none(public_id=worker_id)
    if not worker or not worker.secret_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 Worker")
    if not worker_auth_verifier.check_rate_limit(worker.public_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求频率过高")

    worker_auth_verifier.register_worker_secret(worker.public_id, worker.secret_key)
    payload = {"project_id": project_id}
    if not worker_auth_verifier.verify_signature(
        worker.public_id,
        payload,
        timestamp,
        nonce,
        signature,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="签名验证失败")
    return worker


@router.get("/{project_id}/worker-download", response_model=None)
async def worker_download_project(project_id: str, request: Request):
    worker = await verify_worker_download_request(project_id, request)
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    transfer_info = await project_sync_service.get_project_transfer_info(project.id, project=project)
    logger.info(
        f"Worker [{worker.name}] 下载项目 [{project.name}] "
        f"{transfer_info.get('transfer_method', '')} {transfer_info.get('file_size', 0)}字节"
    )
    return await _download_transfer(transfer_info)


async def _download_transfer(transfer_info: dict[str, Any]):
    transfer_method = str(transfer_info.get("transfer_method") or "")
    if transfer_method.startswith(_S3_TRANSFER_PREFIX):
        return await _download_transfer_from_s3(transfer_info)
    return _download_transfer_from_local(transfer_info)


async def _download_transfer_from_s3(transfer_info: dict[str, Any]):
    from antcode_core.infrastructure.storage.base import get_file_storage_backend
    from antcode_core.infrastructure.storage.presign import generate_download_url

    file_path = _require_transfer_file_path(transfer_info)
    backend = get_file_storage_backend()
    if not await backend.exists(file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")

    try:
        presigned_url = await generate_download_url(file_path, expires_in=3600)
        logger.debug(f"下载重定向到 S3 预签名 URL: {file_path}")
        return RedirectResponse(
            url=presigned_url,
            status_code=302,
            headers=_build_transfer_headers(transfer_info),
        )
    except Exception as exc:
        logger.warning(f"生成预签名 URL 失败，回退到流式下载: {exc}")

    file_size = await backend.get_file_size(file_path)

    async def stream_s3_file() -> AsyncIterator[bytes]:
        async for chunk in backend.open(file_path):
            yield chunk

    return StreamingResponse(
        stream_s3_file(),
        media_type="application/octet-stream",
        headers={
            **_build_transfer_headers(transfer_info),
            "Content-Disposition": f'attachment; filename="{transfer_info.get("original_name") or "project.zip"}"',
            "Content-Length": str(file_size),
        },
    )


def _download_transfer_from_local(transfer_info: dict[str, Any]):
    file_path = _resolve_local_transfer_path(transfer_info)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="下载目标不是文件")

    filename = transfer_info.get("original_name") or os.path.basename(file_path)
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream",
        headers=_build_transfer_headers(transfer_info),
    )


def _resolve_local_transfer_path(transfer_info: dict[str, Any]) -> str:
    file_path = _require_transfer_file_path(transfer_info)
    if transfer_info.get("transfer_method") == _MANAGED_ARCHIVE_METHOD:
        file_path = transfer_info.get("original_file_path") or file_path
    if is_managed_path(file_path):
        return resolve_managed_path(file_path)
    return file_storage_service.get_file_path(file_path)


def _require_transfer_file_path(transfer_info: dict[str, Any]) -> str:
    file_path = (transfer_info.get("file_path") or "").strip()
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目文件路径不存在")
    return file_path


def _build_transfer_headers(transfer_info: dict[str, Any]) -> dict[str, str]:
    headers = {
        "X-Transfer-Method": str(transfer_info.get("transfer_method") or ""),
        "X-File-Hash": str(transfer_info.get("file_hash") or ""),
        "X-File-Size": str(transfer_info.get("file_size") or 0),
    }
    resolved_revision = str(transfer_info.get("resolved_revision") or "")
    if resolved_revision:
        headers["X-Resolved-Revision"] = resolved_revision
    if "modified" in transfer_info:
        headers["X-Is-Modified"] = str(bool(transfer_info.get("modified")))
    return headers
