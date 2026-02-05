"""项目下载API"""

import os
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger

from src.core.security.auth import get_current_user
from src.core.response import success as success_response
from src.models import Project
from src.services.files.file_storage import file_storage_service
from src.services.projects.project_sync_service import project_sync_service
from src.services.users.user_service import user_service

router = APIRouter(prefix="/projects", tags=["项目下载"])


async def _get_request_user(current_user):
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=403, detail="用户不存在或已禁用")
    return user


@router.get("/{project_id}/transfer-info")
async def get_project_transfer_info(
    project_id: str,
    current_user=Depends(get_current_user)
):
    """获取传输策略"""
    user = await _get_request_user(current_user)
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if not user.is_admin and project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    transfer_info = await project_sync_service.get_project_transfer_info(project.id)

    return success_response(transfer_info)


@router.get("/{project_id}/download")
async def download_project_file(
    project_id: str,
    current_user=Depends(get_current_user)
):
    """下载项目文件"""
    user = await _get_request_user(current_user)
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if not user.is_admin and project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    transfer_info = await project_sync_service.get_project_transfer_info(project.id)

    if transfer_info.get("transfer_method") == "code":
        raise HTTPException(
            status_code=400, 
            detail="代码项目请使用/transfer-info获取"
        )

    file_path = transfer_info.get("file_path")
    original_name = transfer_info.get("original_name")
    is_temporary = transfer_info.get("is_temporary", False)

    full_path = file_storage_service.get_file_path(file_path)

    if not file_storage_service.file_exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    logger.info(
        f"下载 [{project.name}] {transfer_info['transfer_method']} "
        f"{transfer_info['file_size']}字节"
    )

    response = FileResponse(
        path=full_path,
        filename=original_name,
        media_type='application/octet-stream',
        headers={
            "X-Transfer-Method": transfer_info["transfer_method"],
            "X-File-Hash": transfer_info["file_hash"],
            "X-File-Size": str(transfer_info["file_size"]),
            "X-Is-Modified": str(transfer_info.get("modified", False)),
        }
    )

    return response


@router.post("/{project_id}/incremental-sync")
async def get_incremental_changes(
    project_id: str,
    client_file_hashes: dict,
    current_user=Depends(get_current_user)
):
    """获取增量变更"""
    user = await _get_request_user(current_user)
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if not user.is_admin and project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    changes = await project_sync_service.get_incremental_changes(
        project.id, 
        client_file_hashes
    )

    return success_response(changes)


@router.get("/{project_id}/download-file")
async def download_specific_file(
    project_id: str,
    file_path: str = Query(..., description="相对路径"),
    current_user=Depends(get_current_user)
):
    """下载单个文件"""
    from src.services.projects.relation_service import relation_service

    user = await _get_request_user(current_user)
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if not user.is_admin and project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    file_detail = await relation_service.get_project_file_detail(project.id)
    if not file_detail or not file_detail.is_compressed:
        raise HTTPException(status_code=400, detail="仅支持压缩项目")

    extracted_path = file_storage_service.get_file_path(file_detail.file_path)
    target_file = os.path.join(extracted_path, file_path)

    if not os.path.abspath(target_file).startswith(os.path.abspath(extracted_path)):
        raise HTTPException(status_code=403, detail="非法路径")

    if not os.path.exists(target_file) or not os.path.isfile(target_file):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=target_file,
        filename=os.path.basename(file_path),
        media_type='application/octet-stream'
    )

@router.get("/{project_id}/node-download")
async def node_download_project(
    project_id: str,
    current_user=Depends(get_current_user)
):
    """节点专用项目下载接口"""
    user = await _get_request_user(current_user)
    project = await Project.get_or_none(public_id=project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if not user.is_admin and project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问")

    transfer_info = await project_sync_service.get_project_transfer_info(project.id)

    if transfer_info.get("transfer_method") == "code":
        raise HTTPException(
            status_code=400, 
            detail="代码项目请使用/transfer-info获取"
        )

    file_path = transfer_info.get("file_path")
    original_name = transfer_info.get("original_name")

    full_path = file_storage_service.get_file_path(file_path)

    if not file_storage_service.file_exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    logger.info(
        f"用户 [{user.username}] 下载项目 [{project.name}] "
        f"{transfer_info['transfer_method']} {transfer_info['file_size']}字节"
    )

    return FileResponse(
        path=full_path,
        filename=original_name,
        media_type='application/octet-stream',
        headers={
            "X-Transfer-Method": transfer_info["transfer_method"],
            "X-File-Hash": transfer_info["file_hash"],
            "X-File-Size": str(transfer_info["file_size"]),
        }
    )
