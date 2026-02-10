"""项目下载API"""

import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse
from loguru import logger

from antcode_web_api.response import success as success_response
from antcode_core.common.security import constant_time_compare
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.common.security.worker_auth import verify_worker_request_with_signature
from antcode_core.domain.models import Project, User, Worker
from antcode_core.application.services.projects.project_sync_service import project_sync_service
from antcode_core.infrastructure.storage.presign import is_s3_storage_enabled

router = APIRouter()
_MAX_INCREMENTAL_FILE_COUNT = 2000
_MAX_RELATIVE_PATH_LEN = 512
_HASH_PATTERN = re.compile(r"^[A-Fa-f0-9]{32,128}$")


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


@router.get("/{project_id}/transfer-info")
async def get_project_transfer_info(
    project_id: str, current_user: TokenData = Depends(get_current_user)
):
    """获取传输策略"""
    project = await _ensure_project_access(project_id, current_user)

    transfer_info = await project_sync_service.get_project_transfer_info(
        project.id,
        project=project,
    )

    return success_response(transfer_info)


@router.get("/{project_id}/download")
async def download_project_file(
    project_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """下载项目文件"""
    project = await _ensure_project_access(project_id, current_user)

    transfer_info = await project_sync_service.get_project_transfer_info(
        project.id,
        project=project,
    )

    if transfer_info.get("transfer_method") == "code":
        raise HTTPException(status_code=400, detail="代码项目请使用/transfer-info获取")

    file_path = transfer_info.get("file_path")
    original_name = transfer_info.get("original_name")

    logger.info(
        f"下载 [{project.name}] {transfer_info['transfer_method']} {transfer_info['file_size']}字节"
    )

    # 检查是否使用 S3 存储
    if is_s3_storage_enabled():
        from antcode_core.infrastructure.storage.base import get_file_storage_backend
        from antcode_core.infrastructure.storage.presign import generate_download_url

        backend = get_file_storage_backend()

        # 检查文件是否存在
        if not await backend.exists(file_path):
            raise HTTPException(status_code=404, detail="文件不存在")

        # 尝试生成预签名 URL 并重定向
        try:
            presigned_url = await generate_download_url(file_path, expires_in=3600)
            logger.debug(f"用户下载重定向到 S3 预签名 URL: {file_path}")
            return RedirectResponse(
                url=presigned_url,
                status_code=302,
                headers={
                    "X-Transfer-Method": transfer_info["transfer_method"],
                    "X-File-Hash": transfer_info["file_hash"],
                    "X-File-Size": str(transfer_info["file_size"]),
                    "X-Is-Modified": str(transfer_info.get("modified", False)),
                }
            )
        except Exception as e:
            logger.warning(f"生成预签名 URL 失败，回退到流式下载: {e}")

        # 回退到流式下载
        try:
            file_size = await backend.get_file_size(file_path)

            async def stream_s3_file() -> AsyncIterator[bytes]:
                async for chunk in backend.open(file_path):
                    yield chunk

            return StreamingResponse(
                stream_s3_file(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{original_name}"',
                    "Content-Length": str(file_size),
                    "X-Transfer-Method": transfer_info["transfer_method"],
                    "X-File-Hash": transfer_info["file_hash"],
                    "X-File-Size": str(transfer_info["file_size"]),
                    "X-Is-Modified": str(transfer_info.get("modified", False)),
                },
            )
        except Exception as e:
            logger.error(f"S3 流式下载失败: {e}")
            raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")

    raise HTTPException(status_code=503, detail="文件项目仅支持 S3 下载")


@router.post("/{project_id}/incremental-sync")
async def get_incremental_changes(
    project_id: str,
    client_file_hashes: dict[str, Any] = Body(...),
    current_user: TokenData = Depends(get_current_user),
):
    """获取增量变更"""
    project = await _ensure_project_access(project_id, current_user)
    validated_hashes = _validate_client_file_hashes(client_file_hashes)

    changes = await project_sync_service.get_incremental_changes(project.id, validated_hashes)

    return success_response(changes)


async def verify_worker_download_request(
    request: Request,
    auth_info: dict = Depends(verify_worker_request_with_signature),
) -> Worker:
    """验证 Worker 下载请求（HMAC 签名 + API Key 双重校验）"""
    worker_id = (auth_info.get("worker_id") or "").strip()
    if not worker_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Worker 标识")

    worker = await Worker.get_or_none(public_id=worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 Worker")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少认证信息")

    api_key = auth_header[7:].strip()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 API Key")

    if not worker.api_key or not constant_time_compare(api_key, worker.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 API Key")

    return worker


@router.get("/{project_id}/worker-download")
async def worker_download_project(
    project_id: str, worker: Worker = Depends(verify_worker_download_request)
):
    """Worker 专用项目下载接口（HMAC 签名 + API Key）

    仅支持 S3 存储后端，优先重定向预签名 URL，失败时回退流式下载。
    """
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    transfer_info = await project_sync_service.get_project_transfer_info(
        project.id,
        project=project,
    )

    if transfer_info.get("transfer_method") == "code":
        raise HTTPException(status_code=400, detail="代码项目请使用/transfer-info获取")

    file_path = transfer_info.get("file_path")
    original_name = transfer_info.get("original_name")

    logger.info(
        f"Worker [{worker.name}] 下载项目 [{project.name}] "
        f"{transfer_info['transfer_method']} {transfer_info['file_size']}字节"
    )

    # 检查是否使用 S3 存储
    if is_s3_storage_enabled():
        return await _download_from_s3_for_worker(
            file_path=file_path,
            original_name=original_name,
            transfer_info=transfer_info,
        )

    raise HTTPException(status_code=503, detail="文件项目仅支持 S3 下载")


async def _download_from_s3_for_worker(
    file_path: str,
    original_name: str,
    transfer_info: dict,
):
    """从 S3 下载文件给 Worker

    优先使用重定向到预签名 URL，回退到流式下载
    """
    from antcode_core.infrastructure.storage.base import get_file_storage_backend
    from antcode_core.infrastructure.storage.presign import generate_download_url

    backend = get_file_storage_backend()

    # 检查文件是否存在
    if not await backend.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    # 尝试生成预签名 URL 并重定向
    try:
        presigned_url = await generate_download_url(file_path, expires_in=3600)
        logger.debug(f"Worker 下载重定向到 S3 预签名 URL: {file_path}")
        return RedirectResponse(
            url=presigned_url,
            status_code=302,
            headers={
                "X-Transfer-Method": transfer_info["transfer_method"],
                "X-File-Hash": transfer_info["file_hash"],
                "X-File-Size": str(transfer_info["file_size"]),
            }
        )
    except Exception as e:
        logger.warning(f"生成预签名 URL 失败，回退到流式下载: {e}")

    # 回退到流式下载
    try:
        file_size = await backend.get_file_size(file_path)

        async def stream_s3_file() -> AsyncIterator[bytes]:
            async for chunk in backend.open(file_path):
                yield chunk

        return StreamingResponse(
            stream_s3_file(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{original_name}"',
                "Content-Length": str(file_size),
                "X-Transfer-Method": transfer_info["transfer_method"],
                "X-File-Hash": transfer_info["file_hash"],
                "X-File-Size": str(transfer_info["file_size"]),
            },
        )
    except Exception as e:
        logger.error(f"S3 流式下载失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")
