from unittest.mock import AsyncMock

import pytest

from scripts import init_db


def _set_required_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://antcode:secret@localhost:5432/antcode")
    monkeypatch.setenv("ENCRYPTION_KEY", "encryption-key")


@pytest.mark.asyncio
async def test_environment_accepts_jwt_secret_file(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "jwt-secret"
    secret_file.write_text("f" * 32, encoding="utf-8")
    _set_required_environment(monkeypatch)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret_file))

    await init_db._check_env()


@pytest.mark.asyncio
async def test_environment_still_accepts_inline_jwt_secret(monkeypatch) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("JWT_SECRET", "i" * 32)
    monkeypatch.delenv("JWT_SECRET_FILE", raising=False)

    await init_db._check_env()


@pytest.mark.asyncio
async def test_environment_rejects_missing_jwt_secret_file(monkeypatch, tmp_path) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("JWT_SECRET_FILE", str(tmp_path / "missing"))

    with pytest.raises(SystemExit) as exc_info:
        await init_db._check_env()

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_environment_rejects_empty_jwt_secret_file(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "jwt-secret"
    secret_file.write_text("\n", encoding="utf-8")
    _set_required_environment(monkeypatch)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret_file))

    with pytest.raises(SystemExit) as exc_info:
        await init_db._check_env()

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_environment_rejects_short_inline_jwt_secret(monkeypatch) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("JWT_SECRET", "too-short")
    monkeypatch.delenv("JWT_SECRET_FILE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        await init_db._check_env()

    assert exc_info.value.code == 1


def test_legacy_worker_upgrade_includes_previous_key_expiry() -> None:
    columns = {column: ddl for column, ddl in init_db.WORKERS_LEGACY_COLUMNS}

    assert "api_key_previous_expires_at" in columns
    assert "TIMESTAMPTZ NULL" in columns["api_key_previous_expires_at"]


@pytest.mark.asyncio
async def test_performance_index_failure_is_not_swallowed(monkeypatch) -> None:
    connection = AsyncMock()
    connection.execute_query.side_effect = RuntimeError("index failed")
    monkeypatch.setattr(init_db, "PERFORMANCE_INDEXES", [("broken", "CREATE INDEX broken")])
    monkeypatch.setattr(
        "antcode_core.infrastructure.db.tortoise.init_db",
        AsyncMock(),
    )
    monkeypatch.setattr("tortoise.connections.get", lambda _name: connection)

    with pytest.raises(RuntimeError, match="index failed"):
        await init_db._create_performance_indexes()


@pytest.mark.asyncio
async def test_system_config_failure_is_not_swallowed(monkeypatch) -> None:
    from antcode_core.application.services.system_config.system_config_service import (
        system_config_service,
    )

    monkeypatch.setattr(
        "antcode_core.infrastructure.db.tortoise.init_db",
        AsyncMock(),
    )
    initialize = AsyncMock(side_effect=RuntimeError("config failed"))
    monkeypatch.setattr(system_config_service, "initialize_default_configs", initialize)

    with pytest.raises(RuntimeError, match="config failed"):
        await init_db._init_system_config()
