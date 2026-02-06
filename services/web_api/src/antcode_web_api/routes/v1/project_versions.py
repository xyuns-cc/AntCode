"""项目版本管理接口

支持：
- 发布草稿为新版本
- 丢弃草稿修改
- 版本列表与回滚
- 编辑状态查询
- 带 ETag 的文件内容操作
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from antcode_web_api.response import Messages
from antcode_web_api.response import success as success_response
from antcode_core.common.security.auth import get_current_user_id
from antcode_core.domain.models.audit_log import AuditAction
from antcode_core.domain.models.enums import ProjectType
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.application.services.audit import audit_service
from antcode_core.application.services.projects.draft_service import project_draft_service
from antcode_core.application.services.projects.project_service import project_service
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.projects.version_service import project_version_service
from antcode_core.application.services.users.user_service import user_service
from antcode_web_api.exceptions import ProjectNotFoundException

project_versions_router = APIRouter()


# ========== 请求/响应模型 ==========


class PublishRequest(BaseModel):
    """发布请求"""
    description: str | None = Field(None, max_length=500, description="版本说明")


class PublishResponse(BaseModel):
    """发布响应"""
    version: int = Field(..., description="版本号")
    version_id: str = Field(..., description="版本 ID")
    artifact_key: str = Field(..., description="artifact S3 路径")
    file_count: int = Field(..., description="文件数量")
    total_size: int = Field(..., description="总大小")


class RollbackRequest(BaseModel):
    """回滚请求"""
    version: int = Field(..., ge=1, description="目标版本号")


class VersionInfo(BaseModel):
    """版本信息"""
    version: int
    version_id: str
    created_at: str
    created_by: int | None
    description: str | None
    file_count: int
    total_size: int
    content_hash: str


class VersionListResponse(BaseModel):
    """版本列表响应"""
    versions: list[VersionInfo]
    total: int


class EditStatusResponse(BaseModel):
    """编辑状态响应"""
    dirty: bool = Field(..., description="是否有未发布修改")
    dirty_files_count: int = Field(..., description="修改文件数")
    last_edit_at: str | None = Field(None, description="最后编辑时间")
    last_editor_id: int | None = Field(None, description="最后编辑者 ID")
    published_version: int = Field(..., description="最新已发布版本号")


class FileContentRequest(BaseModel):
    """文件内容更新请求"""
    path: str = Field(..., max_length=1024, description="文件路径")
    content: str = Field(..., description="文件内容")
    encoding: str = Field("utf-8", max_length=50, description="编码")


class FileContentResponse(BaseModel):
    """文件内容响应"""
    name: str
    path: str
    size: int
    content: str | None = None
    encoding: str = "utf-8"
    etag: str = Field(..., description="ETag 用于并发控制")
    mime_type: str | None = None
    is_text: bool = True


# ========== 辅助函数 ==========


async def _get_project_and_file_detail(project_id: str, user_id: int):
    """获取项目和文件详情"""
    project = await project_service.get_project_by_id(project_id, user_id)
    if not project:
        raise ProjectNotFoundException(project_id)

    if project.type != ProjectType.FILE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只有文件项目支持版本管理",
        )

    file_detail = await relation_service.get_project_file_detail(project.id)
    if not file_detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目文件详情不存在",
        )

    return project, file_detail


# ========== 版本管理接口 ==========


@project_versions_router.post(
    "/{project_id}/publish",
    response_model=BaseResponse[PublishResponse],
    summary="发布草稿为新版本",
    description="将当前草稿发布为不可变版本，生成 artifact.zip",
)
async def publish_draft(
    project_id: str,
    request: PublishRequest,
    http_request: Request,
    current_user_id: int = Depends(get_current_user_id),
):
    """发布草稿为新版本"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    # 发布
    version_record = await project_version_service.publish(
        project_file=file_detail,
        description=request.description,
        user_id=current_user_id,
    )

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_UPDATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"发布版本 v{version_record.version}: {request.description or '无说明'}",
    )

    return success_response(
        PublishResponse(
            version=version_record.version,
            version_id=version_record.version_id,
            artifact_key=version_record.artifact_key,
            file_count=version_record.file_count,
            total_size=version_record.total_size,
        ),
        message=f"版本 v{version_record.version} 发布成功",
    )


@project_versions_router.post(
    "/{project_id}/discard",
    response_model=BaseResponse[None],
    summary="丢弃草稿修改",
    description="丢弃当前草稿的所有修改，恢复到最新已发布版本",
)
async def discard_draft(
    project_id: str,
    http_request: Request,
    current_user_id: int = Depends(get_current_user_id),
):
    """丢弃草稿修改"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    await project_version_service.discard(
        project_file=file_detail,
        user_id=current_user_id,
    )

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_UPDATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description="丢弃草稿修改",
    )

    return success_response(None, message="草稿已丢弃")


@project_versions_router.get(
    "/{project_id}/versions",
    response_model=BaseResponse[VersionListResponse],
    summary="获取版本列表",
    description="获取项目的所有已发布版本",
)
async def list_versions(
    project_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取版本列表"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    versions = await project_version_service.list_versions(project.id)

    return success_response(
        VersionListResponse(
            versions=[VersionInfo(**v) for v in versions],
            total=len(versions),
        ),
        message=Messages.QUERY_SUCCESS,
    )


@project_versions_router.post(
    "/{project_id}/rollback",
    response_model=BaseResponse[None],
    summary="回滚到指定版本",
    description="将草稿回滚到指定版本（不修改历史版本）",
)
async def rollback_to_version(
    project_id: str,
    request: RollbackRequest,
    http_request: Request,
    current_user_id: int = Depends(get_current_user_id),
):
    """回滚到指定版本"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    await project_version_service.rollback(
        project_file=file_detail,
        target_version=request.version,
        user_id=current_user_id,
    )

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_UPDATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"回滚到版本 v{request.version}",
    )

    return success_response(None, message=f"已回滚到版本 v{request.version}")


# ========== 编辑状态接口 ==========


@project_versions_router.get(
    "/{project_id}/edit-status",
    response_model=BaseResponse[EditStatusResponse],
    summary="获取编辑状态",
    description="获取项目的编辑状态（dirty、修改文件数等）",
)
async def get_edit_status(
    project_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取编辑状态"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    status_data = await project_draft_service.get_edit_status(file_detail)

    return success_response(
        EditStatusResponse(**status_data),
        message=Messages.QUERY_SUCCESS,
    )


# ========== 带 ETag 的文件操作接口 ==========


@project_versions_router.get(
    "/{project_id}/draft/files/{file_path:path}",
    response_model=BaseResponse[FileContentResponse],
    summary="获取草稿文件内容（带 ETag）",
    description="获取草稿中指定文件的内容，返回 ETag 用于并发控制",
)
async def get_draft_file_content(
    project_id: str,
    file_path: str,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取草稿文件内容"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    content_bytes, etag = await project_draft_service.get_file_content(file_detail, file_path)

    # 尝试解码为文本
    content_str = None
    encoding = "utf-8"
    is_text = True

    try:
        content_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content_str = content_bytes.decode("gbk")
            encoding = "gbk"
        except UnicodeDecodeError:
            is_text = False

    import mimetypes
    mime_type = mimetypes.guess_type(file_path)[0]

    return success_response(
        FileContentResponse(
            name=file_path.split("/")[-1],
            path=file_path,
            size=len(content_bytes),
            content=content_str,
            encoding=encoding,
            etag=etag,
            mime_type=mime_type,
            is_text=is_text,
        ),
        message=Messages.QUERY_SUCCESS,
    )


@project_versions_router.put(
    "/{project_id}/draft/files/{file_path:path}",
    response_model=BaseResponse[FileContentResponse],
    summary="更新草稿文件内容（带 ETag 并发控制）",
    description="更新草稿中指定文件的内容，需要提供 If-Match header 进行并发控制",
)
async def update_draft_file_content(
    project_id: str,
    file_path: str,
    request: FileContentRequest,
    http_request: Request,
    if_match: str | None = Header(None, description="ETag 用于并发控制"),
    current_user_id: int = Depends(get_current_user_id),
):
    """更新草稿文件内容"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    # 编码内容
    content_bytes = request.content.encode(request.encoding or "utf-8")

    # 更新文件
    new_etag = await project_draft_service.update_file_content(
        project_file=file_detail,
        path=file_path,
        content=content_bytes,
        expected_etag=if_match,
        user_id=current_user_id,
    )

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_UPDATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"编辑文件: {file_path}",
    )

    import mimetypes
    mime_type = mimetypes.guess_type(file_path)[0]

    return success_response(
        FileContentResponse(
            name=file_path.split("/")[-1],
            path=file_path,
            size=len(content_bytes),
            content=request.content,
            encoding=request.encoding,
            etag=new_etag,
            mime_type=mime_type,
            is_text=True,
        ),
        message=Messages.UPDATED_SUCCESS,
    )


@project_versions_router.delete(
    "/{project_id}/draft/files/{file_path:path}",
    response_model=BaseResponse[None],
    summary="删除草稿文件",
    description="删除草稿中的指定文件",
)
async def delete_draft_file(
    project_id: str,
    file_path: str,
    http_request: Request,
    if_match: str | None = Header(None, description="ETag 用于并发控制"),
    current_user_id: int = Depends(get_current_user_id),
):
    """删除草稿文件"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    await project_draft_service.delete_file(
        project_file=file_detail,
        path=file_path,
        expected_etag=if_match,
        user_id=current_user_id,
    )

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_UPDATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"删除文件: {file_path}",
    )

    return success_response(None, message="文件已删除")


@project_versions_router.post(
    "/{project_id}/draft/files/move",
    response_model=BaseResponse[None],
    summary="移动/重命名草稿文件",
    description="移动或重命名草稿中的文件",
)
async def move_draft_file(
    project_id: str,
    request: dict = Body(..., examples=[{"from": "old/path.py", "to": "new/path.py"}]),
    http_request: Request = None,
    current_user_id: int = Depends(get_current_user_id),
):
    """移动/重命名草稿文件"""
    project, file_detail = await _get_project_and_file_detail(project_id, current_user_id)

    from_path = request.get("from")
    to_path = request.get("to")

    if not from_path or not to_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 from 和 to 路径",
        )

    await project_draft_service.move_file(
        project_file=file_detail,
        from_path=from_path,
        to_path=to_path,
        user_id=current_user_id,
    )

    # 记录审计日志
    user = await user_service.get_user_by_id(current_user_id)
    await audit_service.log_project_action(
        action=AuditAction.PROJECT_UPDATE,
        username=user.username if user else "unknown",
        project_id=project.id,
        project_name=project.name,
        user_id=current_user_id,
        ip_address=http_request.client.host if http_request.client else None,
        description=f"移动文件: {from_path} -> {to_path}",
    )

    return success_response(None, message="文件已移动")
