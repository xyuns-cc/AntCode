"""Git repository management routes."""

from __future__ import annotations

import asyncio

from antcode_core.application.services.projects.git_url_security import validate_git_url
from antcode_core.application.services.projects.project_source_service import (
    project_source_service,
)
from antcode_core.application.services.projects.repository_service import (
    RepositoryDeleteStatus,
    RepositoryScanError,
    repository_service,
)
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.common.security.auth import TokenData, get_current_user, get_current_user_id
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.repository import (
    ImportProjectsPayload,
    ImportProjectsResult,
    RepositoryCandidateResponse,
    RepositoryCreateRequest,
    RepositoryResponse,
    RepositoryScanRequest,
    RepositoryScanResponse,
    RepositoryUpdateRequest,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status

from antcode_web_api.response import Messages
from antcode_web_api.response import success as success_response
from antcode_web_api.routes.v1.mutation_audit import AuditedResource, audit_data_imported
from antcode_web_api.routes.v1.runtime_access import (
    ensure_worker_access,
    ensure_worker_admin_access,
)

router = APIRouter()


async def _validated_git_url(url: str) -> str:
    """在写入边界校验 Git URL。

    ``validate_git_url`` 会做 DNS 解析（阻塞），放进线程池。此前建仓/改仓
    完全不校验 URL，``file:///etc/passwd`` 之类能以 201 落库，直到 scan 阶段
    才在 ``resolve_git_url`` 抛 ``ValueError`` 逃逸成 500「服务器内部错误」，
    用户拿不到任何可操作信息。
    """
    try:
        return await asyncio.to_thread(validate_git_url, url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.get("", response_model=BaseResponse[list[RepositoryResponse]])
async def list_repositories(current_user_id: int = Depends(get_current_user_id)):
    repositories = await repository_service.list_for_user(current_user_id)
    return success_response([_repository_response(item) for item in repositories])


@router.post("", response_model=BaseResponse[RepositoryResponse], status_code=status.HTTP_201_CREATED)
async def create_repository(
    payload: RepositoryCreateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    validated = payload.model_copy(update={"url": await _validated_git_url(payload.url)})
    repository = await repository_service.create_for_user(current_user_id, validated)
    return success_response(
        _repository_response(repository),
        message=Messages.CREATED_SUCCESS,
        code=status.HTTP_201_CREATED,
    )


@router.put("/{repository_id}", response_model=BaseResponse[RepositoryResponse])
async def update_repository(
    repository_id: str,
    payload: RepositoryUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    if payload.url is not None:
        payload = payload.model_copy(update={"url": await _validated_git_url(payload.url)})
    repository = await repository_service.update_for_user(repository_id, current_user_id, payload)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Git 仓库不存在")
    return success_response(_repository_response(repository))


@router.delete("/{repository_id}", response_model=BaseResponse[None])
async def delete_repository(
    repository_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    result = await repository_service.delete_for_user(repository_id, current_user_id)
    if result == RepositoryDeleteStatus.NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Git 仓库不存在")
    if result == RepositoryDeleteStatus.IN_USE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Git 仓库仍被项目引用")
    return success_response(None)


@router.post("/{repository_id}/scan", response_model=BaseResponse[RepositoryScanResponse])
async def scan_repository(
    repository_id: str,
    payload: RepositoryScanRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    try:
        repository, candidates = await repository_service.scan_for_user(
            repository_id,
            current_user_id,
            payload.ref,
        )
    except RepositoryScanError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Git 仓库不存在")
    return success_response(
        RepositoryScanResponse(
            repository_id=repository.public_id,
            ref=payload.ref or repository.default_ref,
            candidates=[RepositoryCandidateResponse.model_validate(candidate) for candidate in candidates],
        )
    )


@router.post(
    "/import-from-repository",
    response_model=BaseResponse[ImportProjectsResult],
    status_code=status.HTTP_201_CREATED,
)
async def import_projects_from_repository(
    payload: ImportProjectsPayload,
    current_user_id: int = Depends(get_current_user_id),
    current_user: TokenData = Depends(get_current_user),
    *,
    http_request: Request,
):
    """O4: 从 Git 仓库批量导入项目。

    服务层 ``project_source_service.import_projects`` 已完整实现（get
    enabled repo + Project shell + ProjectCode + upsert_source），
    此路由把它暴露给前端 ``services/repositories.ts.importFromRepository``。
    """
    await _authorize_import_workers(payload.projects, current_user_id)
    try:
        created = await project_source_service.import_projects(
            user_id=current_user_id,
            items=payload.projects,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await audit_data_imported(
        http_request,
        current_user,
        AuditedResource(resource_type="project", resource_name=f"{len(created)} 个项目"),
        scope={
            "repository_ids": sorted({item.repository_id for item in payload.projects}),
            "created_project_ids": created,
        },
    )
    return success_response(
        ImportProjectsResult(created=created),
        message=Messages.CREATED_SUCCESS,
        code=status.HTTP_201_CREATED,
    )


async def _authorize_import_workers(items, user_id: int) -> None:
    for worker_id, admin_required in _worker_access_requirements(items).items():
        if admin_required:
            await ensure_worker_admin_access(worker_id, user_id)
        else:
            await ensure_worker_access(worker_id, user_id)


def _worker_access_requirements(items) -> dict[str, bool]:
    requirements: dict[str, bool] = {}
    for item in items:
        if item.worker_id:
            requirements[item.worker_id] = requirements.get(item.worker_id, False) or bool(item.dependencies)
        if item.bound_worker_id:
            requirements.setdefault(item.bound_worker_id, False)
    return requirements


def _repository_response(repository) -> RepositoryResponse:
    return RepositoryResponse(
        id=repository.public_id,
        name=repository.name,
        url=repository.url,
        default_ref=repository.default_ref,
        credential_id=repository.credential_id,
        enabled=repository.enabled,
        last_scan_status=repository.last_scan_status,
        last_scan_error=normalize_persisted_error_message(repository.last_scan_error),
        last_scan_result=repository.last_scan_result,
        last_scanned_at=repository.last_scanned_at,
        created_at=repository.created_at,
        updated_at=repository.updated_at,
    )
