from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.application.services.workers.registration_cleanup_service import (
    registration_cleanup_service,
)
from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.worker import Worker
from antcode_core.domain.models.worker_install_key import WorkerInstallKey
from antcode_web_api.services.worker_registration import RegistrationConflict, acknowledge_registration
from tortoise import Tortoise


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path, monkeypatch):
    # P1-DB-05: 清理走 worker_service 的临时 Worker 完整撤销链，级联
    # 触及全量模型，注册整个 models 包。
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'registration-cleanup.sqlite3'}",
        modules={"models": ["antcode_core.domain.models"]},
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
    from antcode_core.application.services.workers.worker_service import worker_service

    monkeypatch.setattr(worker_service, "_lease_disabler", AsyncMock(return_value=True))
    yield
    await Tortoise.close_connections()
    await Tortoise._reset_apps()


async def _registration(*, acknowledged: bool = False, expired: bool = True):
    worker = await Worker.create(
        name=f"provisional-{acknowledged}-{expired}",
        host="127.0.0.1",
        port=8001,
        status="connecting",
        api_key_hash="a" * 64,
        secret_key_hash="b" * 64,
        secret_key_encrypted="encrypted-secret",
    )
    now = datetime.now(UTC)
    install_key = await WorkerInstallKey.create(
        key=WorkerInstallKey.hash_plaintext(worker.name),
        status="used",
        os_type="linux",
        created_by=1,
        used_by_worker=worker.public_id,
        used_at=now,
        expires_at=now + timedelta(hours=1),
        registration_id=("a" if acknowledged else "b") * 32,
        recovery_secret_hash="c" * 64,
        registration_request_hash="d" * 64,
        credential_derivation_version=1,
        recovery_expires_at=now + timedelta(seconds=-1 if expired else 60),
        registration_acknowledged_at=now if acknowledged else None,
    )
    return worker, install_key


@pytest.mark.asyncio
async def test_cleanup_revokes_expired_unacknowledged_registration() -> None:
    worker, install_key = await _registration()

    result = await registration_cleanup_service.cleanup_expired(run_settler=AsyncMock(return_value=0))

    assert result.expired_registrations == 1
    assert result.deleted_workers == 1
    assert await Worker.get_or_none(id=worker.id) is None
    persisted = await WorkerInstallKey.get(id=install_key.id)
    assert persisted.status == "expired"
    assert persisted.recovery_secret_hash is None
    assert persisted.registration_request_hash is None
    assert persisted.recovery_expires_at is None
    with pytest.raises(RegistrationConflict, match="恢复窗口已关闭"):
        await acknowledge_registration(
            worker.public_id,
            install_key.registration_id,
            lease_enabler=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_cleanup_settles_active_run_before_deleting_worker() -> None:
    worker, _ = await _registration()
    task = await Task.create(
        name="provisional-active-task",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=1,
        status=TaskStatus.RUNNING,
    )
    run = await TaskRun.create(
        task_id=task.id,
        run_id="provisional-active-run",
        worker_id=worker.id,
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.RUNNING,
        status=TaskStatus.RUNNING,
    )

    async def settle(worker_internal_id: int) -> int:
        assert worker_internal_id == worker.id
        return await TaskRun.filter(id=run.id).update(
            status=TaskStatus.FAILED,
            runtime_status=RuntimeStatus.FAILED,
        )

    result = await registration_cleanup_service.cleanup_expired(run_settler=settle)

    assert result.deleted_workers == 1
    assert await Worker.get_or_none(id=worker.id) is None
    persisted = await TaskRun.get(id=run.id)
    assert persisted.status == TaskStatus.FAILED
    assert persisted.runtime_status == RuntimeStatus.FAILED
    assert persisted.worker_id is None


@pytest.mark.asyncio
async def test_cleanup_preserves_acknowledged_and_unexpired_registrations() -> None:
    acknowledged_worker, _ = await _registration(acknowledged=True)
    active_worker, _ = await _registration(expired=False)

    result = await registration_cleanup_service.cleanup_expired(run_settler=AsyncMock(return_value=0))

    assert result.expired_registrations == 0
    assert result.deleted_workers == 0
    assert await Worker.get_or_none(id=acknowledged_worker.id) is not None
    assert await Worker.get_or_none(id=active_worker.id) is not None


@pytest.mark.asyncio
async def test_cleanup_expires_unused_install_keys_without_workers() -> None:
    now = datetime.now(UTC)
    expired_key = await WorkerInstallKey.create(
        key=WorkerInstallKey.hash_plaintext("expired-unused"),
        status="pending",
        os_type="linux",
        created_by=1,
        expires_at=now - timedelta(seconds=1),
    )
    active_key = await WorkerInstallKey.create(
        key=WorkerInstallKey.hash_plaintext("active-unused"),
        status="pending",
        os_type="linux",
        created_by=1,
        expires_at=now + timedelta(minutes=1),
    )

    result = await registration_cleanup_service.cleanup_expired(run_settler=AsyncMock(return_value=0))

    assert result.expired_pending_keys == 1
    assert (await WorkerInstallKey.get(id=expired_key.id)).status == "expired"
    assert (await WorkerInstallKey.get(id=active_key.id)).status == "pending"
