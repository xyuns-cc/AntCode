from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.schemas.common import PaginationInfo
from antcode_core.domain.schemas.user import (
    UserAdminPasswordUpdateRequest,
    UserBatchStatusRequest,
    UserCreateRequest,
    UserListQueryParams,
)
from antcode_web_api.routes.v1 import users
from fastapi import HTTPException, status
from pydantic import ValidationError


@pytest.mark.asyncio
async def test_user_list_forwards_search_before_pagination(monkeypatch) -> None:
    get_users = AsyncMock(
        return_value={
            "data": {"items": []},
            "pagination": PaginationInfo(page=1, size=20, total=0, pages=0),
        }
    )
    monkeypatch.setattr(users.user_service, "get_users_list", get_users)

    await users.get_users_list(
        query_params=UserListQueryParams(search="user-on-page-two"),
        current_admin=SimpleNamespace(user_id=1),
    )

    assert get_users.await_args.kwargs["search"] == "user-on-page-two"


@pytest.mark.asyncio
async def test_create_user_maps_weak_password_to_bad_request(monkeypatch) -> None:
    monkeypatch.setattr(users.user_service, "get_user_by_id", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(users.user_service, "create_user", AsyncMock(side_effect=ValueError("密码强度不足")))
    request = UserCreateRequest(username="alice", password="weakpass", is_admin=False)

    with pytest.raises(HTTPException) as exc_info:
        await users.create_user(request, SimpleNamespace(client=None), SimpleNamespace(user_id=1))

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "密码强度不足"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_status"),
    [("用户不存在", status.HTTP_404_NOT_FOUND), ("密码强度不足", status.HTTP_400_BAD_REQUEST)],
)
async def test_reset_password_maps_user_errors(monkeypatch, message: str, expected_status: int) -> None:
    monkeypatch.setattr(users.user_service, "reset_user_password", AsyncMock(side_effect=ValueError(message)))

    with pytest.raises(HTTPException) as exc_info:
        await users.reset_user_password(
            "missing-user",
            UserAdminPasswordUpdateRequest(new_password="Strong#123"),
            current_admin=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail == message


@pytest.mark.parametrize("value", ["false", "0", 0, 1])
def test_batch_status_rejects_non_boolean_status(value: object) -> None:
    with pytest.raises(ValidationError):
        UserBatchStatusRequest(user_ids=["user-1"], is_active=value)


def test_batch_status_accepts_json_boolean() -> None:
    request = UserBatchStatusRequest(user_ids=["user-1"], is_active=False)

    assert request.is_active is False


def test_batch_status_rejects_boolean_user_id() -> None:
    with pytest.raises(ValidationError):
        UserBatchStatusRequest(user_ids=[True], is_active=False)
