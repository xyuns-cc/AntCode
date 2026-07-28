from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from antcode_core.application.services.workers.registration_cleanup_service import (
    registration_cleanup_service,
)
from antcode_core.domain.models.worker import Worker
from antcode_core.domain.models.worker_install_key import WorkerInstallKey
from antcode_web_api.services.worker_registration import RegistrationConflict, acknowledge_registration
from tortoise import Tortoise


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path):
    # P1-DB-05: 清理走 worker_service.delete_worker 完整撤销链，级联触及
    # 全量模型，注册整个 models 包。
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'registration-cleanup.sqlite3'}",
        modules={"models": ["antcode_core.domain.models"]},
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
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

    result = await registration_cleanup_service.cleanup_expired()

    assert result.expired_registrations == 1
    assert result.deleted_workers == 1
    assert await Worker.get_or_none(id=worker.id) is None
    persisted = await WorkerInstallKey.get(id=install_key.id)
    assert persisted.status == "expired"
    assert persisted.recovery_secret_hash is None
    assert persisted.registration_request_hash is None
    assert persisted.recovery_expires_at is None
    with pytest.raises(RegistrationConflict, match="恢复窗口已关闭"):
        await acknowledge_registration(worker.public_id, install_key.registration_id)


@pytest.mark.asyncio
async def test_cleanup_preserves_acknowledged_and_unexpired_registrations() -> None:
    acknowledged_worker, _ = await _registration(acknowledged=True)
    active_worker, _ = await _registration(expired=False)

    result = await registration_cleanup_service.cleanup_expired()

    assert result.expired_registrations == 0
    assert result.deleted_workers == 0
    assert await Worker.get_or_none(id=acknowledged_worker.id) is not None
    assert await Worker.get_or_none(id=active_worker.id) is not None
