"""项目下载API"""

import os
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from loguru import logger

from antcode_web_api.response import success as success_response
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models import Project, User, Worker
from antcode_core.application.services.files.file_storage import file_storage_service
from antcode_core.application.services.projects.project_sync_service import project_sync_service
from antcode_core.infrastructure.storage.presign import is_s3_storage_enabled

router = APIRouter()


@router.get("/{project_id}/transfer-info")
async def get_project_transfer_info(
    project_id: str, current_user: User = Depends(get_current_user)
):
    """获取传输策略"""
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    transfer_info = await project_sync_service.get_project_transfer_info(
        project.id,
        project=project,
    )

    return success_response(transfer_info)


@router.get("/{project_id}/download")
async def download_project_file(project_id: str, current_user: User = Depends(get_current_user)):
    """下载项目文件"""
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问")

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

    # 本地文件系统下载
    full_path = file_storage_service.get_file_path(file_path)

    if not await file_storage_service.file_exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    response = FileResponse(
        path=full_path,
        filename=original_name,
        media_type="application/octet-stream",
        headers={
            "X-Transfer-Method": transfer_info["transfer_method"],
            "X-File-Hash": transfer_info["file_hash"],
            "X-File-Size": str(transfer_info["file_size"]),
            "X-Is-Modified": str(transfer_info.get("modified", False)),
        },
    )

    return response


@router.post("/{project_id}/incremental-sync")
async def get_incremental_changes(
    project_id: str,
    client_file_hashes: dict,
    current_user: User = Depends(get_current_user),
):
    """获取增量变更"""
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    changes = await project_sync_service.get_incremental_changes(project.id, client_file_hashes)

    return success_response(changes)


@router.get("/{project_id}/download-file")
async def download_specific_file(
    project_id: str,
    file_path: str = Query(..., description="相对路径"),
    current_user: User = Depends(get_current_user),
):
    """下载单个文件（支持本地和 S3 存储）"""
    from antcode_core.application.services.projects.relation_service import relation_service

    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    file_detail = await relation_service.get_project_file_detail(project.id)
    if not file_detail or not file_detail.is_compressed:
        raise HTTPException(status_code=400, detail="仅支持压缩项目")

    # 检查是否使用 S3 存储且是项目目录
    if is_s3_storage_enabled() and file_detail.file_path.startswith("projects/"):
        from antcode_core.infrastructure.storage.base import get_file_storage_backend
        from antcode_core.infrastructure.storage.presign import generate_download_url

        backend = get_file_storage_backend()

        # 构建 S3 key
        s3_key = f"{file_detail.file_path.rstrip('/')}/{file_path.lstrip('/')}"

        # 检查文件是否存在
        if not await backend.exists(s3_key):
            raise HTTPException(status_code=404, detail="文件不存在")

        # 尝试重定向到预签名 URL
        try:
            presigned_url = await generate_download_url(s3_key, expires_in=3600)
            return RedirectResponse(url=presigned_url, status_code=302)
        except Exception as e:
            logger.warning(f"生成预签名 URL 失败，回退到流式下载: {e}")

        # 回退到流式下载
        try:
            file_size = await backend.get_file_size(s3_key)

            async def stream_s3_file() -> AsyncIterator[bytes]:
                async for chunk in backend.open(s3_key):
                    yield chunk

            return StreamingResponse(
                stream_s3_file(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{os.path.basename(file_path)}"',
                    "Content-Length": str(file_size),
                },
            )
        except Exception as e:
            logger.error(f"S3 流式下载失败: {e}")
            raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")

    # 本地文件系统下载
    extracted_path = file_storage_service.get_file_path(file_detail.file_path)
    target_file = os.path.join(extracted_path, file_path)

    if not os.path.abspath(target_file).startswith(os.path.abspath(extracted_path)):
        raise HTTPException(status_code=403, detail="非法路径")

    if not os.path.exists(target_file) or not os.path.isfile(target_file):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=target_file,
        filename=os.path.basename(file_path),
        media_type="application/octet-stream",
    )


async def verify_worker_api_key(request: Request) -> Worker:
    """验证 Worker API Key"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少认证信息")

    api_key = auth_header[7:]  # 去掉 "Bearer " 前缀

    worker = await Worker.get_or_none(api_key=api_key)
    if not worker:
        raise HTTPException(status_code=401, detail="无效的 API Key")

    return worker


@router.get("/{project_id}/worker-download")
async def worker_download_project(
    project_id: str, worker: Worker = Depends(verify_worker_api_key)
):
    """Worker 专用项目下载接口（使用 Worker API Key 认证）

    支持本地文件系统和 S3 存储后端：
    - 本地存储：直接返回文件
    - S3 存储：重定向到预签名 URL 或流式下载
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

    # 本地文件系统下载
    full_path = file_storage_service.get_file_path(file_path)

    if not await file_storage_service.file_exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=full_path,
        filename=original_name,
        media_type="application/octet-stream",
        headers={
            "X-Transfer-Method": transfer_info["transfer_method"],
            "X-File-Hash": transfer_info["file_hash"],
            "X-File-Size": str(transfer_info["file_size"]),
        },
    )


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
