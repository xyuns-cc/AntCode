"""Git 凭证接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from antcode_core.common.security.auth import get_current_user_id
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.git_credential import (
    GitCredentialCreateRequest,
    GitCredentialResponse,
    GitCredentialUpdateRequest,
)
from antcode_core.application.services.projects.git_credential_service import (
    git_credential_service,
)
from antcode_web_api.response import Messages, success as success_response

router = APIRouter()


def _to_response(credential) -> GitCredentialResponse:
    return GitCredentialResponse(
        id=credential.public_id,
        name=credential.name,
        auth_type=credential.auth_type,
        username=credential.username,
        host_scope=credential.host_scope,
        has_secret=bool(credential.secret_encrypted),
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


@router.get("", response_model=BaseResponse[list[GitCredentialResponse]], summary="列出 Git 凭证")
async def list_git_credentials(current_user_id: int = Depends(get_current_user_id)):
    credentials = await git_credential_service.list_for_user(current_user_id)
    return success_response([_to_response(item) for item in credentials], message=Messages.QUERY_SUCCESS)


@router.post(
    "",
    response_model=BaseResponse[GitCredentialResponse],
    status_code=status.HTTP_201_CREATED,
    summary="创建 Git 凭证",
)
async def create_git_credential(
    payload: GitCredentialCreateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    credential = await git_credential_service.create_for_user(current_user_id, payload)
    return success_response(_to_response(credential), message=Messages.CREATED_SUCCESS, code=201)


@router.get(
    "/{credential_id}",
    response_model=BaseResponse[GitCredentialResponse],
    summary="获取 Git 凭证详情",
)
async def get_git_credential(
    credential_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    credential = await git_credential_service.get_for_user(credential_id, current_user_id)
    if credential is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Git 凭证不存在")
    return success_response(_to_response(credential), message=Messages.QUERY_SUCCESS)


@router.put(
    "/{credential_id}",
    response_model=BaseResponse[GitCredentialResponse],
    summary="更新 Git 凭证",
)
async def update_git_credential(
    credential_id: str,
    payload: GitCredentialUpdateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    credential = await git_credential_service.update_for_user(credential_id, current_user_id, payload)
    if credential is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Git 凭证不存在")
    return success_response(_to_response(credential), message=Messages.UPDATED_SUCCESS)


@router.delete(
    "/{credential_id}",
    response_model=BaseResponse[None],
    summary="删除 Git 凭证",
)
async def delete_git_credential(
    credential_id: str,
    current_user_id: int = Depends(get_current_user_id),
):
    deleted = await git_credential_service.delete_for_user(credential_id, current_user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Git 凭证不存在")
    return success_response(None, message=Messages.DELETED_SUCCESS)
