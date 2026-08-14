from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.users.user_service import User, user_cache, user_service


class _UserQuery:
    def __init__(self) -> None:
        self.filters: list[tuple[object, ...]] = []

    def filter(self, *args, **_kwargs) -> "_UserQuery":
        self.filters.append(args)
        return self

    async def count(self) -> int:
        return 0

    def order_by(self, *_fields) -> "_UserQuery":
        return self

    def offset(self, _offset: int) -> "_UserQuery":
        return self

    async def limit(self, _size: int) -> list[object]:
        return []


@pytest.mark.asyncio
async def test_user_search_is_applied_before_count_and_pagination(monkeypatch) -> None:
    query = _UserQuery()
    monkeypatch.setattr(User, "all", lambda: query)
    monkeypatch.setattr(user_cache, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(user_cache, "set", AsyncMock())

    result = await user_service.get_users_list(
        page=1,
        size=20,
        search="  Alice  ",
    )

    search_query = query.filters[0][0]
    child_filters = [child.filters for child in search_query.children]
    assert child_filters == [
        {"public_id__icontains": "Alice"},
        {"username__icontains": "Alice"},
    ]
    assert result["pagination"].total == 0


def test_user_list_cache_key_includes_search() -> None:
    alice_key = user_service._generate_cache_key(page=1, size=20, search="alice")
    bob_key = user_service._generate_cache_key(page=1, size=20, search="bob")

    assert alice_key != bob_key
