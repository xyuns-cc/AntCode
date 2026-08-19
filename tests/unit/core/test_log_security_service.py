from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.logs.log_security_service as log_security_module
import pytest
import pytest_asyncio
from antcode_core.application.services.logs.log_security_service import LogSecurityService
from antcode_core.domain.models import CrawlBatch, TaskRun, User
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from antcode_core.domain.models.user import UserRole
from fastapi import HTTPException, status
from tortoise import Tortoise

EXPECTED_ROLE_AUTHORIZATION_CHECKS = 2
BATCH_OWNER_ID = 7
STRANGER_ID = 8
BATCH_PUBLIC_ID = "batch-1"
BATCH_RUN_ID = "crawl-batch-run"


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
    monkeypatch.setattr(service, "_find_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(log_security_module, "resolve_run_owner_id", AsyncMock(return_value=99))
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
    monkeypatch.setattr(service, "_find_execution", AsyncMock(return_value=execution))
    monkeypatch.setattr(log_security_module, "resolve_run_owner_id", AsyncMock(return_value=99))
    monkeypatch.setattr(log_security_module.QueryHelper, "is_admin", AsyncMock(return_value=True))
    admin = SimpleNamespace(user_id=7, role="admin", is_admin=True)

    assert await service.verify_log_access_permission(admin, "run-id", "read") is execution
    with pytest.raises(HTTPException) as exc_info:
        await service.verify_log_access_permission(admin, "run-id", "write")

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest_asyncio.fixture
async def log_access_tables():
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["antcode_core.domain.models"]})
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _seed_batch_run() -> None:
    await User.create(id=BATCH_OWNER_ID, username="owner", password_hash="x", role=UserRole.USER)
    await CrawlBatch.create(public_id=BATCH_PUBLIC_ID, project_id=11, name="批次", user_id=BATCH_OWNER_ID)
    await TaskRun.create(
        run_id=BATCH_RUN_ID,
        task_id=TASK_ID_ABSENT,
        status=TaskStatus.RUNNING,
        dispatch_status=DispatchStatus.PENDING,
        result_data={"crawl_batch_id": BATCH_PUBLIC_ID},
    )


@pytest.mark.asyncio
async def test_batch_run_owner_can_read_own_logs(log_access_tables) -> None:
    """回归: 批次 run 没有 Task 行，``Task.get`` 抛 DoesNotExist → 所有人（含管理员）404。"""
    await _seed_batch_run()
    owner = SimpleNamespace(user_id=BATCH_OWNER_ID, role="user", is_admin=False)

    execution = await LogSecurityService().verify_log_access_permission(owner, BATCH_RUN_ID)

    assert execution.run_id == BATCH_RUN_ID


@pytest.mark.asyncio
async def test_stranger_cannot_read_foreign_batch_run_logs(log_access_tables) -> None:
    """放行批次 run 不等于放弃鉴权。"""
    await _seed_batch_run()
    await User.create(id=STRANGER_ID, username="stranger", password_hash="x", role=UserRole.USER)
    stranger = SimpleNamespace(user_id=STRANGER_ID, role="user", is_admin=False)

    with pytest.raises(HTTPException) as exc_info:
        await LogSecurityService().verify_log_access_permission(stranger, BATCH_RUN_ID)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_orphan_run_logs_stay_refused(log_access_tables) -> None:
    """哨兵豁免不得退化：真丢 Task 行的孤儿 run 照旧 404。"""
    await User.create(id=BATCH_OWNER_ID, username="owner", password_hash="x", role=UserRole.USER)
    await TaskRun.create(
        run_id="orphan-run",
        task_id=999,
        status=TaskStatus.RUNNING,
        dispatch_status=DispatchStatus.PENDING,
    )
    owner = SimpleNamespace(user_id=BATCH_OWNER_ID, role="user", is_admin=False)

    with pytest.raises(HTTPException) as exc_info:
        await LogSecurityService().verify_log_access_permission(owner, "orphan-run")

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


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
