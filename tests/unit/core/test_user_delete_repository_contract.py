from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.users import user_cascade_delete

USER_DELETE = Path("packages/antcode_core/src/antcode_core/application/services/users/user_cascade_delete.py")


class _DeleteQuery:
    def __init__(self, label: str, events: list[str]) -> None:
        self.label = label
        self.events = events

    def using_db(self, _connection):
        return self

    async def delete(self) -> int:
        self.events.append(self.label)
        return 1


class _DeleteModel:
    def __init__(self, label: str, events: list[str]) -> None:
        self.label = label
        self.events = events

    def filter(self, **_filters):
        return _DeleteQuery(self.label, self.events)


class _TransactionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


def test_hard_user_delete_removes_repositories_before_credentials_and_user() -> None:
    source = USER_DELETE.read_text(encoding="utf-8")
    repository_delete = "GitRepository.filter(owner_user_id=user.id).using_db(connection).delete()"
    credential_delete = "GitCredential.filter(owner_user_id=user.id).using_db(connection).delete()"
    user_delete = "locked_user.delete(using_db=connection)"

    assert repository_delete in source
    assert credential_delete in source
    assert source.index(repository_delete) < source.index(credential_delete) < source.index(user_delete)


def test_hard_user_delete_reports_repository_cleanup_count() -> None:
    source = USER_DELETE.read_text(encoding="utf-8")

    assert '"git_repositories": 0' in source
    assert 'deleted["git_repositories"]' in source


@pytest.mark.asyncio
async def test_user_cleanup_executes_repository_delete_before_credentials(monkeypatch) -> None:
    events: list[str] = []
    models = {
        "UserWorkerPermission": "permission",
        "GitRepository": "repository",
        "GitCredential": "credential",
        "UserSession": "session",
    }
    for name, label in models.items():
        monkeypatch.setattr(user_cascade_delete, name, _DeleteModel(label, events))
    monkeypatch.setattr(user_cascade_delete, "in_transaction", lambda: _TransactionContext())

    async def delete(*, using_db) -> None:
        assert using_db is not None
        events.append("user")

    locked_user = SimpleNamespace(delete=delete)
    lock_user = AsyncMock(return_value=locked_user)
    monkeypatch.setattr(user_cascade_delete, "lock_user", lock_user)

    counts = await user_cascade_delete.cascade_delete_user_data(SimpleNamespace(id=7))

    assert counts["git_repositories"] == 1
    assert events == ["permission", "repository", "credential", "session", "user"]
    lock_user.assert_awaited_once()
