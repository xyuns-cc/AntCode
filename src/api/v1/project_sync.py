"""同步回调API"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from loguru import logger

from src.core.security.node_auth import verify_node_request_with_signature
from src.core.security.auth import get_current_user
from src.core.response import success as success_response
from src.services.nodes.node_project_service import node_project_service


router = APIRouter(prefix="/sync", tags=["项目同步"])


class SyncCallbackRequest(BaseModel):
    """同步回调"""
    project_public_id: str
    node_local_project_id: str
    file_hash: str
    file_size: int
    transfer_method: str
    success: bool
    error: Optional[str] = None


class SyncStatusRequest(BaseModel):
    """状态查询"""
    project_public_id: str


@router.post("/callback")
async def sync_callback(
    request: SyncCallbackRequest,
    auth_info: dict = Depends(verify_node_request_with_signature),
    current_user=Depends(get_current_user)
):
    """同步完成回调（HMAC签名验证）"""
    from src.models import Node
    node_id = auth_info.get("node_id")
    node = await Node.filter(public_id=node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    try:
        if request.success:
            from src.models import Project
            project = await Project.get_or_none(public_id=request.project_public_id)

            if project:
                await node_project_service.record_project_sync(
                    node_id=node.id,
                    project_id=project.id,
                    project_public_id=request.project_public_id,
                    file_hash=request.file_hash,
                    file_size=request.file_size,
                    transfer_method=request.transfer_method,
                    node_local_project_id=request.node_local_project_id,
                )

                logger.info(
                    f"同步成功 [{node.name}@{request.project_public_id}] "
                    f"本地ID:{request.node_local_project_id}"
                )

                return success_response({
                    "recorded": True,
                    "node_id": node.public_id,
                    "project_id": request.project_public_id
                })
            else:
                logger.warning(f"项目不存在: {request.project_public_id}")
                return success_response({"recorded": False, "error": "项目不存在"})
        else:
            logger.error(
                f"同步失败 [{node.name}@{request.project_public_id}] "
                f"{request.error}"
            )

            node_project = await node_project_service.check_node_has_project(
                node.id, request.project_public_id
            )
            if node_project:
                node_project.status = "failed"
                await node_project.save()

            return success_response({
                "recorded": True,
                "status": "failed",
                "error": request.error
            })

    except Exception as e:
        logger.error(f"回调处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{project_public_id}")
async def get_sync_status(
    project_public_id: str,
    auth_info: dict = Depends(verify_node_request_with_signature),
    current_user=Depends(get_current_user)
):
    """查询同步状态（HMAC签名验证）"""
    from src.models import Node
    node_id = auth_info.get("node_id")
    node = await Node.filter(public_id=node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    try:
        node_project = await node_project_service.check_node_has_project(
            node.id, project_public_id
        )

        if not node_project:
            return success_response({
                "exists": False,
                "status": "not_found"
            })

        return success_response({
            "exists": True,
            "status": node_project.status,
            "file_hash": node_project.file_hash,
            "file_size": node_project.file_size,
            "synced_at": node_project.synced_at.isoformat() if node_project.synced_at else None,
            "sync_count": node_project.sync_count,
            "last_used_at": node_project.last_used_at.isoformat() if node_project.last_used_at else None,
        })

    except Exception as e:
        logger.error(f"状态查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/statistics")
async def get_sync_statistics(
    auth_info: dict = Depends(verify_node_request_with_signature),
    current_user=Depends(get_current_user)
):
    """获取同步统计（HMAC签名验证）"""
    from src.models import Node
    node_id = auth_info.get("node_id")
    node = await Node.filter(public_id=node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    try:
        stats = await node_project_service.get_sync_statistics(node.id)
        return success_response(stats)
    except Exception as e:
        logger.error(f"统计获取失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
