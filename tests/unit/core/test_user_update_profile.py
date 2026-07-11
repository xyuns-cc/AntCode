from types import SimpleNamespace

import pytest
from antcode_core.application.services.users.user_service import UserService
from antcode_core.domain.models.user import User


@pytest.mark.asyncio
async def test_update_user_rejects_existing_username(monkeypatch):
    service = UserService()

    target_user = SimpleNamespace(id=1, username="admin", email="admin@example.com")

    async def fake_get_user_by_public_id(_user_id):
        return target_user

    existing_user = SimpleNamespace(id=2, username="taken", email="taken@example.com")

    async def fake_get_or_none(**kwargs):
        if kwargs.get("username") == "taken":
            return existing_user
        if kwargs.get("email") == "taken@example.com":
            return existing_user
        return None

    monkeypatch.setattr(service, "get_user_by_public_id", fake_get_user_by_public_id)
    monkeypatch.setattr(User, "get_or_none", fake_get_or_none)

    request = SimpleNamespace(model_dump=lambda exclude_unset=True: {"username": "taken"})

    with pytest.raises(Exception) as exc_info:
        await service.update_user("u-1", request)

    assert "用户名已存在" in str(exc_info.value)


@pytest.mark.asyncio
async def test_update_user_requires_correct_old_password(monkeypatch):
    service = UserService()

    class DummyUser:
        id = 1
        username = "admin"
        email = "admin@example.com"

        def verify_password(self, value):
            return value == "Correct#123"

        def set_password(self, _value):
            pass

        async def save(self):
            return None

    async def fake_get_user_by_public_id(_user_id):
        return DummyUser()

    async def fake_get(**_kwargs):
        return DummyUser()

    monkeypatch.setattr(service, "get_user_by_public_id", fake_get_user_by_public_id)
    monkeypatch.setattr(User, "get", fake_get)

    request = SimpleNamespace(old_password="wrong", new_password="Valid#123")

    with pytest.raises(ValueError) as exc_info:
        await service.update_user_password("u-1", request, current_user_id=1)

    assert "当前密码错误" in str(exc_info.value)
