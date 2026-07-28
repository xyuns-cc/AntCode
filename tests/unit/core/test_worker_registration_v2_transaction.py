from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from antcode_core.common.config import settings
from antcode_core.domain.models.worker import Worker
from antcode_core.domain.models.worker_install_key import WorkerInstallKey
from antcode_core.domain.schemas.worker import WorkerRegisterByKeyV2Request
from antcode_web_api.services.worker_registration import (
    RegistrationConflict,
    RegistrationExpired,
    RegistrationForbidden,
    RegistrationNotFound,
    acknowledge_registration,
    register_or_recover,
)
from tortoise import Tortoise


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "registration-test-encryption-key-material")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "registration-test-salt")
    monkeypatch.setattr(settings, "WORKER_REGISTRATION_RECOVERY_SECONDS", 3600)
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'worker-registration-v2.sqlite3'}",
        modules={
            "models": [
                "antcode_core.domain.models.worker",
                "antcode_core.domain.models.worker_install_key",
            ]
        },
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()
    await Tortoise._reset_apps()


async def _pending_key(plaintext: str = "RECOVERABLE-INSTALL-KEY") -> WorkerInstallKey:
    return await WorkerInstallKey.create(
        key=WorkerInstallKey.hash_plaintext(plaintext),
        os_type="linux",
        created_by=42,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    )


def _request(*, recovery_secret: str = "b" * 64) -> WorkerRegisterByKeyV2Request:
    return WorkerRegisterByKeyV2Request(
        key="RECOVERABLE-INSTALL-KEY",
        name="worker-v2",
        host="127.0.0.1",
        port=8001,
        region="test",
        transport_mode="gateway",
        client_timestamp=1,
        client_nonce="nonce-123",
        registration_id="a" * 32,
        recovery_secret=recovery_secret,
    )


@pytest.mark.asyncio
async def test_v2_registration_recovers_same_worker_and_credentials() -> None:
    install_key = await _pending_key()
    request = _request()

    created = await register_or_recover(request, install_key, "127.0.0.1")
    recovered = await register_or_recover(request, install_key, "127.0.0.1")

    assert created.recovered is False
    assert recovered.recovered is True
    assert recovered.worker_id == created.worker_id
    assert recovered.api_key == created.api_key
    assert recovered.secret_key == created.secret_key
    assert await Worker.all().count() == 1
    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.registration_id == request.registration_id
    assert persisted_key.recovery_secret_hash
    assert persisted_key.registration_request_hash


@pytest.mark.asyncio
async def test_v2_registration_rejects_wrong_recovery_secret() -> None:
    install_key = await _pending_key()
    await register_or_recover(_request(), install_key, "127.0.0.1")
    persisted_key = await WorkerInstallKey.get(id=install_key.id)

    with pytest.raises(RegistrationForbidden, match="恢复秘密"):
        await register_or_recover(_request(recovery_secret="c" * 64), persisted_key, "127.0.0.1")


@pytest.mark.asyncio
async def test_v2_registration_ack_is_idempotent_and_closes_recovery() -> None:
    install_key = await _pending_key()
    request = _request()
    created = await register_or_recover(request, install_key, "127.0.0.1")

    first_ack = await acknowledge_registration(created.worker_id, request.registration_id)
    repeated_ack = await acknowledge_registration(created.worker_id, request.registration_id)

    assert repeated_ack == first_ack
    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.recovery_secret_hash is None
    assert persisted_key.recovery_expires_at is None
    with pytest.raises(RegistrationConflict, match="恢复窗口已关闭"):
        await register_or_recover(request, persisted_key, "127.0.0.1")


@pytest.mark.asyncio
async def test_v2_registration_rolls_back_when_worker_save_fails(monkeypatch) -> None:
    install_key = await _pending_key()

    async def fail_save(*_args, **_kwargs):
        raise OSError("database write failed")

    monkeypatch.setattr(Worker, "save", fail_save)
    with pytest.raises(OSError, match="database write failed"):
        await register_or_recover(_request(), install_key, "127.0.0.1")

    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.status == "pending"
    assert persisted_key.registration_id is None
    assert await Worker.all().count() == 0


@pytest.mark.asyncio
async def test_v2_registration_maps_duplicate_worker_name_to_conflict() -> None:
    await Worker.create(name="worker-v2", host="127.0.0.2", port=8002)
    install_key = await _pending_key()

    with pytest.raises(RegistrationConflict, match="Worker 名称"):
        await register_or_recover(_request(), install_key, "127.0.0.1")

    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.status == "pending"
    assert persisted_key.registration_id is None
    assert await Worker.all().count() == 1


@pytest.mark.asyncio
async def test_v2_recovery_rejects_changed_request_fingerprint() -> None:
    install_key = await _pending_key()
    request = _request()
    await register_or_recover(request, install_key, "127.0.0.1")
    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    changed_request = request.model_copy(update={"host": "127.0.0.2"})

    with pytest.raises(RegistrationConflict, match="首次注册参数"):
        await register_or_recover(changed_request, persisted_key, "127.0.0.1")


@pytest.mark.asyncio
async def test_v2_recovery_rejects_changed_request_source() -> None:
    install_key = await _pending_key()
    request = _request()
    await register_or_recover(request, install_key, "127.0.0.1")
    persisted_key = await WorkerInstallKey.get(id=install_key.id)

    with pytest.raises(RegistrationForbidden, match="来源"):
        await register_or_recover(request, persisted_key, "127.0.0.2")


@pytest.mark.asyncio
async def test_v2_recovery_rejects_expired_window() -> None:
    install_key = await _pending_key()
    request = _request()
    await register_or_recover(request, install_key, "127.0.0.1")
    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    persisted_key.recovery_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await persisted_key.save(update_fields=["recovery_expires_at"])

    with pytest.raises(RegistrationExpired, match="恢复窗口已过期"):
        await register_or_recover(request, persisted_key, "127.0.0.1")


@pytest.mark.asyncio
async def test_v2_recovery_rejects_tampered_worker_credentials() -> None:
    install_key = await _pending_key()
    request = _request()
    created = await register_or_recover(request, install_key, "127.0.0.1")
    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    worker = await Worker.get(public_id=created.worker_id)
    worker.api_key_hash = "0" * 64
    await worker.save(update_fields=["api_key_hash"])

    with pytest.raises(RegistrationConflict, match="当前身份不一致"):
        await register_or_recover(request, persisted_key, "127.0.0.1")


@pytest.mark.asyncio
async def test_v2_registration_rejects_duplicate_registration_id_across_keys() -> None:
    first_key = await _pending_key()
    await register_or_recover(_request(), first_key, "127.0.0.1")
    second_plaintext = "SECOND-RECOVERABLE-INSTALL-KEY"
    second_key = await _pending_key(second_plaintext)
    second_request = _request().model_copy(
        update={"key": second_plaintext, "name": "worker-v2-second"},
    )

    with pytest.raises(RegistrationConflict, match="registration_id"):
        await register_or_recover(second_request, second_key, "127.0.0.1")

    persisted_key = await WorkerInstallKey.get(id=second_key.id)
    assert persisted_key.status == "pending"
    assert persisted_key.registration_id is None
    assert await Worker.all().count() == 1


@pytest.mark.asyncio
async def test_v2_registration_ack_rejects_missing_or_wrong_worker() -> None:
    with pytest.raises(RegistrationNotFound, match="不存在"):
        await acknowledge_registration("worker-missing", "f" * 32)

    install_key = await _pending_key()
    request = _request()
    await register_or_recover(request, install_key, "127.0.0.1")
    with pytest.raises(RegistrationForbidden, match="身份不匹配"):
        await acknowledge_registration("worker-other", request.registration_id)
