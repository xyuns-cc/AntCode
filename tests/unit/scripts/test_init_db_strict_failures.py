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


def test_schema_contract_includes_redis_acl_columns() -> None:
    from scripts.init_db_schema_contracts import COLUMN_CONTRACTS

    expected = {
        "redis_username",
        "redis_password_encrypted",
        "redis_acl_revision",
        "redis_acl_synced_at",
    }
    contract_columns = {item.name for item in COLUMN_CONTRACTS if item.table == "workers"}
    assert expected.issubset(contract_columns)


def test_required_indexes_are_created_concurrently() -> None:
    indexes = dict(init_db.PERFORMANCE_INDEXES)

    assert "CONCURRENTLY" in indexes["idx_task_executions_cancel_requested_at"]
    assert "UNIQUE INDEX CONCURRENTLY" in indexes["idx_worker_install_keys_registration_id_unique"]
    assert "CONCURRENTLY" in indexes["idx_project_rules_region"]
    assert "CONCURRENTLY" in indexes["idx_scheduled_tasks_project_id"]
    assert "CONCURRENTLY" in indexes["idx_project_sources_repository_subdir"]


@pytest.mark.asyncio
async def test_standard_init_validates_schema_only_after_it_is_built(monkeypatch) -> None:
    events: list[str] = []

    def step(name: str):
        async def run() -> None:
            events.append(name)

        return run

    monkeypatch.setattr(init_db, "load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr(init_db, "_check_env", step("environment"))
    for name in (
        "_generate_schemas",
        "_align_database_integrity",
        "_check_required_tables",
        "_create_performance_indexes",
        "_validate_schema_contracts",
        "_init_system_config",
        "_create_admin",
    ):
        monkeypatch.setattr(init_db, name, step(name))
    monkeypatch.setenv("DATABASE_URL", "postgresql://antcode:secret@localhost:5432/antcode")

    await init_db.main()

    assert events == [
        "environment",
        "_generate_schemas",
        "_align_database_integrity",
        "_check_required_tables",
        "_create_performance_indexes",
        "_validate_schema_contracts",
        "_init_system_config",
        "_create_admin",
    ]


def test_worker_project_models_are_registered_and_required() -> None:
    from antcode_core.domain.models import WorkerProject, WorkerProjectFile

    assert WorkerProject._meta.db_table == "worker_projects"
    assert WorkerProjectFile._meta.db_table == "worker_project_files"
    assert {"worker_projects", "worker_project_files"}.issubset(init_db.REQUIRED_TABLES)


def test_public_ids_use_only_the_unique_index() -> None:
    from antcode_core.domain.models import GitCredential, GitRepository, Project, TaskRun, User

    for model in (GitCredential, GitRepository, Project, TaskRun, User):
        field = model._meta.fields_map["public_id"]
        assert field.unique is True
        assert field.index is False


@pytest.mark.asyncio
async def test_performance_index_failure_is_not_swallowed(monkeypatch) -> None:
    _set_required_environment(monkeypatch)
    connection = AsyncMock()
    connection.execute_query.side_effect = RuntimeError("index failed")
    monkeypatch.setattr(init_db, "PERFORMANCE_INDEXES", [("broken", "CREATE INDEX broken")])
    monkeypatch.setattr("antcode_core.infrastructure.db.tortoise.init_db", AsyncMock())
    close_db = AsyncMock()
    monkeypatch.setattr("antcode_core.infrastructure.db.tortoise.close_db", close_db)
    monkeypatch.setattr("tortoise.connections.get", lambda _name: connection)

    with pytest.raises(RuntimeError, match="index failed"):
        await init_db._create_performance_indexes()
    close_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_config_failure_is_not_swallowed(monkeypatch) -> None:
    _set_required_environment(monkeypatch)
    from antcode_core.application.services.system_config.system_config_service import (
        system_config_service,
    )

    monkeypatch.setattr("antcode_core.infrastructure.db.tortoise.init_db", AsyncMock())
    close_db = AsyncMock()
    monkeypatch.setattr("antcode_core.infrastructure.db.tortoise.close_db", close_db)
    initialize = AsyncMock(side_effect=RuntimeError("config failed"))
    monkeypatch.setattr(system_config_service, "initialize_default_configs", initialize)

    with pytest.raises(RuntimeError, match="config failed"):
        await init_db._init_system_config()
    close_db.assert_awaited_once()
