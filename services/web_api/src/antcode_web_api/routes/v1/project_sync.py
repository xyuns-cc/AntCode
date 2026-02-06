"""同步回调API"""


from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from antcode_web_api.response import success as success_response
from antcode_core.common.security.worker_auth import verify_worker_request_with_signature
from antcode_core.application.services.workers.worker_project_service import (
    worker_project_service,
)

router = APIRouter()


class SyncCallbackRequest(BaseModel):
    """同步回调"""

    project_public_id: str
    worker_local_project_id: str
    file_hash: str
    file_size: int
    transfer_method: str
    success: bool
    error: str | None = None


class SyncStatusRequest(BaseModel):
    """状态查询"""

    project_public_id: str


@router.post("/callback")
async def sync_callback(
    request: SyncCallbackRequest,
    auth_info: dict = Depends(verify_worker_request_with_signature),
):
    """同步完成回调（HMAC签名验证）"""
    from antcode_core.domain.models import Worker

    worker_id = auth_info.get("worker_id")
    worker = await Worker.filter(public_id=worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    try:
        if request.success:
            from antcode_core.domain.models import Project

            project = await Project.get_or_none(public_id=request.project_public_id)

            if project:
                await worker_project_service.record_project_sync(
                    worker_id=worker.id,
                    project_id=project.id,
                    project_public_id=request.project_public_id,
                    file_hash=request.file_hash,
                    file_size=request.file_size,
                    transfer_method=request.transfer_method,
                    worker_local_project_id=request.worker_local_project_id,
                )

                logger.info(
                    f"同步成功 [{worker.name}@{request.project_public_id}] "
                    f"本地ID:{request.worker_local_project_id}"
                )

                return success_response(
                    {
                        "recorded": True,
                        "worker_id": worker.public_id,
                        "project_id": request.project_public_id,
                    }
                )
            else:
                logger.warning(f"项目不存在: {request.project_public_id}")
                return success_response({"recorded": False, "error": "项目不存在"})
        else:
            logger.error(
                f"同步失败 [{worker.name}@{request.project_public_id}] {request.error}"
            )

            worker_project = await worker_project_service.check_worker_has_project(
                worker.id, request.project_public_id
            )
            if worker_project:
                worker_project.status = "failed"
                await worker_project.save()

            return success_response({"recorded": True, "status": "failed", "error": request.error})

    except Exception as e:
        logger.error(f"回调处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{project_public_id}")
async def get_sync_status(
    project_public_id: str,
    auth_info: dict = Depends(verify_worker_request_with_signature),
):
    """查询同步状态（HMAC签名验证）"""
    from antcode_core.domain.models import Worker

    worker_id = auth_info.get("worker_id")
    worker = await Worker.filter(public_id=worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    try:
        worker_project = await worker_project_service.check_worker_has_project(
            worker.id, project_public_id
        )

        if not worker_project:
            return success_response({"exists": False, "status": "not_found"})

        return success_response(
            {
                "exists": True,
                "status": worker_project.status,
                "file_hash": worker_project.file_hash,
                "file_size": worker_project.file_size,
                "synced_at": worker_project.synced_at.isoformat() if worker_project.synced_at else None,
                "sync_count": worker_project.sync_count,
                "last_used_at": worker_project.last_used_at.isoformat()
                if worker_project.last_used_at
                else None,
            }
        )

    except Exception as e:
        logger.error(f"状态查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/statistics")
async def get_sync_statistics(
    auth_info: dict = Depends(verify_worker_request_with_signature),
):
    """获取同步统计（HMAC签名验证）"""
    from antcode_core.domain.models import Worker

    worker_id = auth_info.get("worker_id")
    worker = await Worker.filter(public_id=worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    try:
        stats = await worker_project_service.get_sync_statistics(worker.id)
        return success_response(stats)
    except Exception as e:
        logger.error(f"统计获取失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
