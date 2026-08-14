from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.users import user_ownership_lock

REPOSITORY_SERVICE = Path("packages/antcode_core/src/antcode_core/application/services/projects/repository_service.py")
USER_DELETE = Path("packages/antcode_core/src/antcode_core/application/services/users/user_cascade_delete.py")


def _user_query(result):
    query = MagicMock()
    query.using_db.return_value = query
    query.select_for_update.return_value = query
    query.only.return_value = query
    query.first = AsyncMock(return_value=result)
    return query


@pytest.mark.asyncio
async def test_owned_resource_creation_locks_active_user(monkeypatch) -> None:
    user = MagicMock(id=7)
    query = _user_query(user)
    user_filter = MagicMock(return_value=query)
    monkeypatch.setattr(user_ownership_lock.User, "filter", user_filter)
    connection = object()

    result = await user_ownership_lock.lock_user(connection, 7, active_only=True)

    assert result is user
    user_filter.assert_called_once_with(id=7, is_active=True)
    query.using_db.assert_called_once_with(connection)
    query.select_for_update.assert_called_once_with()


@pytest.mark.asyncio
async def test_owned_resource_creation_rejects_deleted_user(monkeypatch) -> None:
    monkeypatch.setattr(user_ownership_lock.User, "filter", MagicMock(return_value=_user_query(None)))

    with pytest.raises(ValueError, match="用户不存在或已停用"):
        await user_ownership_lock.lock_user(object(), 9, active_only=True)


def test_repository_create_and_user_delete_share_user_row_lock() -> None:
    create_source = REPOSITORY_SERVICE.read_text(encoding="utf-8")
    delete_source = USER_DELETE.read_text(encoding="utf-8")

    assert create_source.index("lock_user(connection, user_id, active_only=True)") < create_source.index(
        "GitRepository.create("
    )
    assert "lock_user(connection, user.id, active_only=False)" in delete_source
