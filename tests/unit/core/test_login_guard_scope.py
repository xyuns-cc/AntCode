from unittest.mock import AsyncMock

import pytest
from antcode_core.common.security import login_guard


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True

    async def exists(self, key: str) -> bool:
        return key in self.values

    async def set(self, key: str, value: int, **_kwargs) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    async def ttl(self, key: str) -> int:
        return 900 if key in self.values else -2


@pytest.mark.asyncio
async def test_one_client_cannot_lock_account_for_other_clients(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(login_guard, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(login_guard.settings, "LOGIN_LOCKOUT_FAILURES", 2)

    await login_guard.record_failure("admin", "192.0.2.10")
    _, locked = await login_guard.record_failure("admin", "192.0.2.10")

    assert locked is True
    assert await login_guard.is_account_locked("admin", "192.0.2.10") == (True, 900)
    assert await login_guard.is_account_locked("admin", "192.0.2.11") == (False, 0)
