import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from antcode_core.domain.models.worker import Worker
from antcode_core.domain.models.worker_install_key import WorkerInstallKey
from antcode_web_api.routes.v1 import workers as workers_route
from tortoise import Tortoise


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path):
    database_path = tmp_path / "worker-install-key.sqlite3"
    await Tortoise.init(
        db_url=f"sqlite://{database_path}",
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


def _request(name: str, plaintext_key: str, *, transport_mode: str = "gateway") -> SimpleNamespace:
    return SimpleNamespace(
        key=plaintext_key,
        name=name,
        host="127.0.0.1",
        port=8001,
        region="test",
        transport_mode=transport_mode,
    )


async def _pending_key(
    plaintext_key: str,
    *,
    expired: bool = False,
    allowed_source: str | None = None,
) -> WorkerInstallKey:
    expires_at = datetime.now(UTC) + (-timedelta(minutes=1) if expired else timedelta(hours=1))
    return await WorkerInstallKey.create(
        key=WorkerInstallKey.hash_plaintext(plaintext_key),
        os_type="linux",
        created_by=42,
        allowed_source=allowed_source,
        expires_at=expires_at,
        status="pending",
    )


@pytest.mark.asyncio
async def test_create_install_key_persists_canonical_allowed_source() -> None:
    install_key = await WorkerInstallKey.create_install_key(
        os_type="linux",
        created_by=42,
        allowed_source="10.8.7.6/24",
    )

    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.allowed_source == "10.8.7.0/24"


@pytest.mark.asyncio
async def test_registration_transaction_commits_worker_and_real_key_owner() -> None:
    plaintext_key = "TRANSACTION-SUCCESS-KEY"
    install_key = await _pending_key(plaintext_key)

    with (
        patch.object(workers_route, "store_api_key"),
        patch.object(workers_route, "store_secret_key"),
    ):
        worker, _, _ = await workers_route._create_worker_from_install_key(
            _request("worker-transaction-success", plaintext_key),
            install_key,
            "127.0.0.1",
        )

    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.status == "used"
    assert persisted_key.used_by_worker == worker.public_id
    assert await Worker.filter(public_id=worker.public_id).count() == 1


@pytest.mark.asyncio
async def test_direct_install_key_registration_persists_issue_eligibility() -> None:
    plaintext_key = "TRANSACTION-DIRECT-KEY"
    install_key = await _pending_key(plaintext_key)

    with (
        patch.object(workers_route, "store_api_key"),
        patch.object(workers_route, "store_secret_key"),
    ):
        worker, _, _ = await workers_route._create_worker_from_install_key(
            _request("worker-direct", plaintext_key, transport_mode="direct"),
            install_key,
            "127.0.0.1",
        )

    persisted = await Worker.get(id=worker.id)
    assert persisted.transport_mode == "direct"


@pytest.mark.asyncio
async def test_registration_transaction_rolls_back_when_finalize_updates_no_row() -> None:
    plaintext_key = "TRANSACTION-ROLLBACK-KEY"
    install_key = await _pending_key(plaintext_key)

    with (
        patch.object(workers_route, "store_api_key"),
        patch.object(workers_route, "store_secret_key"),
        patch.object(
            WorkerInstallKey,
            "finalize_claim",
            AsyncMock(return_value=0),
        ),
    ):
        with pytest.raises(RuntimeError, match="真实 Worker ID 回写失败"):
            await workers_route._create_worker_from_install_key(
                _request("worker-transaction-rollback", plaintext_key),
                install_key,
                "127.0.0.1",
            )

    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.status == "pending"
    assert persisted_key.used_by_worker is None
    assert persisted_key.used_at is None
    assert await Worker.filter(name="worker-transaction-rollback").count() == 0


@pytest.mark.asyncio
async def test_expired_pending_key_cannot_win_cas() -> None:
    plaintext_key = "TRANSACTION-EXPIRED-KEY"
    install_key = await _pending_key(plaintext_key, expired=True)

    claimed = await WorkerInstallKey.cas_claim_pending(
        plaintext_key,
        "pending:test",
        allowed_source="127.0.0.1",
    )

    assert claimed is False
    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.status == "pending"
    assert persisted_key.used_by_worker is None


@pytest.mark.asyncio
async def test_concurrent_cas_has_exactly_one_winner() -> None:
    plaintext_key = "TRANSACTION-CONCURRENT-KEY"
    install_key = await _pending_key(plaintext_key)

    results = await asyncio.gather(
        WorkerInstallKey.cas_claim_pending(
            plaintext_key,
            "pending:first",
            allowed_source="10.0.0.1",
        ),
        WorkerInstallKey.cas_claim_pending(
            plaintext_key,
            "pending:second",
            allowed_source="10.0.0.2",
        ),
    )

    assert sorted(results) == [False, True]
    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.used_by_worker in {"pending:first", "pending:second"}
    expected_source = "10.0.0.1" if results[0] else "10.0.0.2"
    assert persisted_key.allowed_source == expected_source


@pytest.mark.asyncio
async def test_registration_persists_first_source_when_key_is_unrestricted() -> None:
    plaintext_key = "TRANSACTION-FIRST-SOURCE-KEY"
    install_key = await _pending_key(plaintext_key)

    with (
        patch.object(workers_route, "store_api_key"),
        patch.object(workers_route, "store_secret_key"),
    ):
        await workers_route._create_worker_from_install_key(
            _request("worker-first-source", plaintext_key),
            install_key,
            "2001:db8::7",
        )

    persisted_key = await WorkerInstallKey.get(id=install_key.id)
    assert persisted_key.allowed_source == "2001:db8::7"
