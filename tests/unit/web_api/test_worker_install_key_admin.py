from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from antcode_core.domain.models import User, WorkerInstallKey
from antcode_core.domain.models.user import UserRole
from antcode_web_api.routes.v1 import workers_install_key_admin as module
from fastapi import HTTPException
from tortoise import Tortoise

HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE_ENTITY = 422


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path):
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'install-key-admin.sqlite3'}",
        modules={"models": ["antcode_core.domain.models.user", "antcode_core.domain.models.worker_install_key"]},
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()
    await Tortoise._reset_apps()


async def _admin():
    user = await User.create(
        username="admin",
        password_hash="hash",
        is_active=True,
        role=UserRole.ADMIN,
    )
    return SimpleNamespace(user_id=user.id)


async def _pending_key(plaintext: str = "PENDING-KEY") -> WorkerInstallKey:
    return await WorkerInstallKey.create(
        key=WorkerInstallKey.hash_plaintext(plaintext),
        status="pending",
        os_type="linux",
        created_by=1,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_list_does_not_expose_install_key_hash() -> None:
    key = await _pending_key()

    response = await module.list_install_keys(1, 20, await _admin())

    assert response.data.pagination.total == 1
    item = response.data.items[0]
    assert item.id == key.public_id
    assert "key" not in item.model_dump()


@pytest.mark.asyncio
async def test_revoke_pending_key_is_idempotent() -> None:
    key = await _pending_key()
    admin = await _admin()

    first = await module.revoke_install_key(key.public_id, admin)
    second = await module.revoke_install_key(key.public_id, admin)

    assert first.data["status"] == "revoked"
    assert second.data["status"] == "revoked"
    assert (await WorkerInstallKey.get(id=key.id)).status == "revoked"


@pytest.mark.asyncio
async def test_revoke_consumed_key_is_conflict() -> None:
    key = await _pending_key()
    key.status = "used"
    await key.save(update_fields=["status"])

    with pytest.raises(HTTPException) as exc_info:
        await module.revoke_install_key(key.public_id, await _admin())

    assert exc_info.value.status_code == HTTP_CONFLICT


@pytest.mark.asyncio
async def test_revoke_expired_pending_key_marks_expired_and_conflicts() -> None:
    key = await _pending_key()
    key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await key.save(update_fields=["expires_at"])

    with pytest.raises(HTTPException) as exc_info:
        await module.revoke_install_key(key.public_id, await _admin())

    assert exc_info.value.status_code == HTTP_CONFLICT
    assert (await WorkerInstallKey.get(id=key.id)).status == "expired"


@pytest.mark.asyncio
async def test_revoke_rejects_invalid_public_id() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await module.revoke_install_key("1 OR 1=1", await _admin())

    assert exc_info.value.status_code == HTTP_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_generate_build_failure_does_not_persist_key(monkeypatch) -> None:
    monkeypatch.setattr(module.WorkerInstallKey, "generate_key", classmethod(lambda cls: "K" * 32))

    def fail_build(*_args, **_kwargs):
        raise RuntimeError("build failed")

    monkeypatch.setattr(module, "build_worker_install_command", fail_build)
    request = SimpleNamespace(os_type="linux", allowed_source=None)
    workers = SimpleNamespace(load_worker_install_config=lambda _settings: object())

    with pytest.raises(RuntimeError, match="build failed"):
        await module.generate_install_key(request, SimpleNamespace(), await _admin(), workers_module=workers)

    assert await WorkerInstallKey.all().count() == 0


async def _generate(monkeypatch, allowed_source):
    """跑一次 generate_install_key，返回 response。"""
    monkeypatch.setattr(module.WorkerInstallKey, "generate_key", classmethod(lambda cls: "K" * 32))
    monkeypatch.setattr(module, "build_worker_install_command", lambda *_a, **_k: "install.sh")
    request = SimpleNamespace(os_type="linux", allowed_source=allowed_source)
    workers = SimpleNamespace(load_worker_install_config=lambda _settings: object())
    return await module.generate_install_key(request, SimpleNamespace(), await _admin(), workers_module=workers)


@pytest.mark.parametrize("bad_source", ["antcode.example.com", "localhost", "10.0.0.256", "not an ip", "10.0.0.0/33"])
@pytest.mark.asyncio
async def test_generate_rejects_non_ip_allowed_source(monkeypatch, bad_source: str) -> None:
    """兑换侧只认 IP/CIDR：非法值必须在生成时 400，而不是产出一枚永远无法兑换的 Key。"""
    with pytest.raises(HTTPException) as exc_info:
        await _generate(monkeypatch, bad_source)

    assert exc_info.value.status_code == HTTP_BAD_REQUEST
    assert await WorkerInstallKey.all().count() == 0


@pytest.mark.parametrize("good_source", ["10.0.0.5", "10.0.0.0/24", "192.168.1.10", "::1"])
@pytest.mark.asyncio
async def test_generate_accepts_ip_and_cidr_allowed_source(monkeypatch, good_source: str) -> None:
    response = await _generate(monkeypatch, good_source)

    assert response.data.allowed_source == good_source
    assert (await WorkerInstallKey.all().first()).allowed_source == good_source


@pytest.mark.parametrize("empty_source", [None, "", "   "])
@pytest.mark.asyncio
async def test_generate_treats_blank_allowed_source_as_unbound(monkeypatch, empty_source) -> None:
    """留空 = 不预先绑定（由兑换侧 TOFU 兜底），持久化为 None 而非空串。"""
    response = await _generate(monkeypatch, empty_source)

    assert response.data.allowed_source is None
    assert (await WorkerInstallKey.all().first()).allowed_source is None
