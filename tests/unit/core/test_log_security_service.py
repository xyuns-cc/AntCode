from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.logs.log_security_service as log_security_module
import pytest
from antcode_core.application.services.logs.log_security_service import LogSecurityService
from fastapi import HTTPException, status

EXPECTED_ROLE_AUTHORIZATION_CHECKS = 2


@pytest.mark.asyncio
async def test_log_access_rate_limit_preserves_http_429(monkeypatch) -> None:
    service = LogSecurityService()
    monkeypatch.setattr(service, "_check_rate_limit", lambda _user_id: False)

    with pytest.raises(HTTPException) as exc_info:
        await service.verify_log_access_permission(SimpleNamespace(user_id=7), "run-id")

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert exc_info.value.detail == "访问频率过高，请稍后再试"


@pytest.mark.asyncio
async def test_log_permission_cache_is_scoped_to_live_role(monkeypatch) -> None:
    service = LogSecurityService()
    execution = SimpleNamespace(task_id=11)
    task = SimpleNamespace(user_id=99)
    monkeypatch.setattr(service, "_find_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(log_security_module.Task, "get", AsyncMock(return_value=task))
    is_admin = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(log_security_module.QueryHelper, "is_admin", is_admin)

    admin = SimpleNamespace(user_id=7, role="admin", is_admin=True)
    downgraded = SimpleNamespace(user_id=7, role="user", is_admin=False)

    assert await service.verify_log_access_permission(admin, "run-id") is execution
    with pytest.raises(HTTPException) as exc_info:
        await service.verify_log_access_permission(downgraded, "run-id")

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert is_admin.await_count == EXPECTED_ROLE_AUTHORIZATION_CHECKS


@pytest.mark.asyncio
async def test_read_cache_does_not_bypass_write_permission(monkeypatch) -> None:
    service = LogSecurityService()
    execution = SimpleNamespace(task_id=11)
    task = SimpleNamespace(user_id=99)
    monkeypatch.setattr(service, "_find_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(log_security_module.Task, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(log_security_module.QueryHelper, "is_admin", AsyncMock(return_value=True))
    admin = SimpleNamespace(user_id=7, role="admin", is_admin=True)

    assert await service.verify_log_access_permission(admin, "run-id", "read") is execution
    with pytest.raises(HTTPException) as exc_info:
        await service.verify_log_access_permission(admin, "run-id", "write")

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_log_permission_cache_purges_all_expired_scopes(monkeypatch) -> None:
    service = LogSecurityService()
    service._permission_cache = {
        "perm:7:admin:1:read:run-old": {"timestamp": 100.0, "has_permission": True},
        "perm:7:user:0:read:run-old": {"timestamp": 200.0, "has_permission": False},
    }
    monkeypatch.setattr(log_security_module.time, "time", lambda: 1_000.0)

    assert service._get_cached_permission("perm:7:user:0:read:run-new") is None
    assert service._permission_cache == {}


def test_clear_permission_cache_removes_all_operations_and_scopes() -> None:
    service = LogSecurityService()
    service._permission_cache = {
        "perm:7:admin:1:read:run-old": {"timestamp": 100.0},
        "perm:7:admin:1:write:run-old": {"timestamp": 100.0},
        "perm:7:user:0:read:run-old": {"timestamp": 100.0},
        "perm:7:user:0:read:run-keep": {"timestamp": 100.0},
    }

    service.clear_permission_cache(user_id=7, run_id="run-old")

    assert set(service._permission_cache) == {"perm:7:user:0:read:run-keep"}
