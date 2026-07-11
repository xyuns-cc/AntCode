"""Git repository management routes."""

from __future__ import annotations

from antcode_core.application.services.projects.project_source_service import (
    project_source_service,
)
from antcode_core.application.services.projects.repository_service import repository_service
from antcode_core.common.security.auth import get_current_user_id
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
from fastapi import APIRouter, Depends, HTTPException, status

from antcode_web_api.response import success as success_response

router = APIRouter()


@router.get("", response_model=BaseResponse[list[RepositoryResponse]])
async def list_repositories(current_user_id: int = Depends(get_current_user_id)):
    repositories = await repository_service.list_for_user(current_user_id)
    return success_response([_repository_response(item) for item in repositories])


@router.post("", response_model=BaseResponse[RepositoryResponse], status_code=status.HTTP_201_CREATED)
async def create_repository(
    payload: RepositoryCreateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    repository = await repository_service.create_for_user(current_user_id, payload)
    return success_response(_repository_response(repository))


@router.put("/{repository_id}", response_model=BaseResponse[RepositoryResponse])
async def update_repository(
    repository_id: str,
    payload: RepositoryUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    repository = await repository_service.update_for_user(repository_id, current_user_id, payload)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Git 仓库不存在")
    return success_response(_repository_response(repository))


@router.post("/{repository_id}/scan", response_model=BaseResponse[RepositoryScanResponse])
async def scan_repository(
    repository_id: str,
    payload: RepositoryScanRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    repository, candidates = await repository_service.scan_for_user(
        repository_id,
        current_user_id,
        payload.ref,
    )
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
):
    """O4: 从 Git 仓库批量导入项目。

    服务层 ``project_source_service.import_projects`` 已完整实现（get
    enabled repo + Project shell + ProjectCode + upsert_source），
    此路由把它暴露给前端 ``services/repositories.ts.importFromRepository``。
    """
    try:
        created = await project_source_service.import_projects(
            user_id=current_user_id,
            items=payload.projects,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return success_response(ImportProjectsResult(created=created))


def _repository_response(repository) -> RepositoryResponse:
    return RepositoryResponse(
        id=repository.public_id,
        name=repository.name,
        url=repository.url,
        default_ref=repository.default_ref,
        credential_id=repository.credential_id,
        enabled=repository.enabled,
        last_scan_status=repository.last_scan_status,
        last_scan_error=repository.last_scan_error,
        last_scan_result=repository.last_scan_result,
        last_scanned_at=repository.last_scanned_at,
        created_at=repository.created_at,
        updated_at=repository.updated_at,
    )
