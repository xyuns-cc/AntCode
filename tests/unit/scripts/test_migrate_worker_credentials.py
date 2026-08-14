from __future__ import annotations

from contextlib import AbstractAsyncContextManager

import pytest
from antcode_core.common.config import settings
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.secret_box import secret_box
from cryptography.fernet import Fernet

from scripts import migrate_worker_credentials as migration

EXPECTED_PLAINTEXT_SCHEMA_QUERIES = 3


class _Connection:
    def __init__(
        self,
        columns: set[str],
        rows: list[dict] | None = None,
        *,
        update_affected: int = 1,
    ) -> None:
        self.columns = columns
        self.rows = rows or []
        self.update_affected = update_affected
        self.updates: list[list[object]] = []
        self.update_sql: list[str] = []
        self.locked = False
        self.altered = False
        self.query_count = 0

    async def execute_query_dict(self, sql: str):
        if "information_schema.columns" in sql:
            self.query_count += 1
            if self.altered:
                return []
            return [{"column_name": column} for column in self.columns]
        if 'FROM public."workers"' in sql:
            return [dict(row) for row in self.rows]
        raise AssertionError(f"unexpected query: {sql}")

    async def execute_query(self, sql: str, params: list[object] | None = None):
        if sql.startswith('LOCK TABLE public."workers"'):
            self.locked = True
            return 0, []
        if sql.startswith('UPDATE public."workers"'):
            self.update_sql.append(sql)
            self.updates.append(params or [])
            return self.update_affected, []
        if sql.startswith('ALTER TABLE public."workers"'):
            self.altered = True
            return 0, []
        raise AssertionError(f"unexpected query: {sql}")


class _Transaction(AbstractAsyncContextManager):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


def _configure_encryption(monkeypatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    secret_box._cached = None
    secret_box._cache_key = None
    return key


@pytest.mark.asyncio
async def test_plaintext_column_detection_returns_exact_schema_set() -> None:
    connection = _Connection({"api_key", "secret_key", "api_key_previous"})

    columns = await migration._plaintext_columns(connection)

    assert columns == {"api_key", "secret_key", "api_key_previous"}


@pytest.mark.asyncio
async def test_transactional_migration_is_idempotent_when_plaintext_columns_are_absent() -> None:
    connection = _Connection(set())

    assert await migration.migrate_worker_credentials(connection) == 0
    assert connection.locked is False
    assert connection.altered is False


@pytest.mark.asyncio
async def test_migrate_rows_hashes_and_encrypts_real_credentials(monkeypatch) -> None:
    _configure_encryption(monkeypatch)
    connection = _Connection(
        set(),
        [
            {
                "id": 7,
                "api_key": "api-current",
                "secret_key": "hmac-secret",
                "api_key_previous": "api-old",
            },
            {"id": 8, "api_key": None, "secret_key": None, "api_key_previous": None},
        ],
    )

    migrated = await migration._migrate_rows(connection)

    assert migrated == 2
    current = connection.updates[0]
    assert current[0] == hash_api_key("api-current")
    assert current[1] == hash_api_key("hmac-secret")
    assert secret_box.decrypt(str(current[2])) == "hmac-secret"
    assert current[3:] == [hash_api_key("api-old"), 7]
    assert connection.updates[1] == [None, None, None, None, 8]
    assert connection.locked is True


@pytest.mark.asyncio
async def test_migrate_rows_never_replaces_new_credentials_with_null(monkeypatch) -> None:
    _configure_encryption(monkeypatch)
    connection = _Connection(
        set(),
        [{"id": 8, "api_key": None, "secret_key": None, "api_key_previous": None}],
    )

    await migration._migrate_rows(connection)

    sql = connection.update_sql[0]
    assert 'COALESCE("api_key_hash", $1)' in sql
    assert 'COALESCE("secret_key_hash", $2)' in sql
    assert 'COALESCE("secret_key_encrypted", $3)' in sql
    assert 'COALESCE("api_key_previous_hash", $4)' in sql
    assert connection.updates == [[None, None, None, None, 8]]


@pytest.mark.asyncio
async def test_missing_encryption_key_fails_before_database_initialization(monkeypatch) -> None:
    initialized = False

    async def init_db(*, service: str) -> None:
        nonlocal initialized
        initialized = True

    from antcode_core.infrastructure.db import tortoise as db_module

    monkeypatch.setattr(migration, "load_dotenv", lambda _path: None)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(db_module, "init_db", init_db)

    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        await migration.main()

    assert initialized is False


@pytest.mark.asyncio
async def test_incomplete_plaintext_schema_is_rejected(monkeypatch) -> None:
    _configure_encryption(monkeypatch)
    connection = _Connection({"api_key", "secret_key"})
    events = _install_database(monkeypatch, connection)

    with pytest.raises(RuntimeError, match="明文凭据列不完整"):
        await migration.main()

    assert connection.altered is False
    assert events == [("init", "web_api"), "close"]


@pytest.mark.asyncio
async def test_transactional_migration_rejects_incomplete_plaintext_schema() -> None:
    connection = _Connection({"api_key", "secret_key"})

    with pytest.raises(RuntimeError, match="明文凭据列不完整"):
        await migration.migrate_worker_credentials(connection)

    assert connection.locked is False
    assert connection.altered is False


@pytest.mark.asyncio
@pytest.mark.parametrize("affected", [0, 2])
async def test_unexpected_update_count_aborts_before_drop(monkeypatch, affected: int) -> None:
    _configure_encryption(monkeypatch)
    columns = {"api_key", "secret_key", "api_key_previous"}
    rows = [{"id": 1, "api_key": "api", "secret_key": "secret", "api_key_previous": None}]
    connection = _Connection(columns, rows, update_affected=affected)
    events = _install_database(monkeypatch, connection)

    with pytest.raises(RuntimeError, match=rf"id=1, affected={affected}"):
        await migration.main()

    assert connection.altered is False
    assert events == [("init", "web_api"), "close"]


@pytest.mark.asyncio
async def test_main_migrates_rows_then_drops_plaintext_columns(monkeypatch, capsys) -> None:
    _configure_encryption(monkeypatch)
    columns = {"api_key", "secret_key", "api_key_previous"}
    rows = [{"id": 1, "api_key": "api", "secret_key": "secret", "api_key_previous": None}]
    connection = _Connection(columns, rows)
    events = _install_database(monkeypatch, connection)

    await migration.main()

    assert events == [("init", "web_api"), "close"]
    assert len(connection.updates) == 1
    assert connection.altered is True
    assert connection.query_count == EXPECTED_PLAINTEXT_SCHEMA_QUERIES
    assert "已安全迁移 1 个 Worker 的凭据" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_database_query_failure_is_propagated() -> None:
    class _FailingConnection:
        async def execute_query_dict(self, _sql: str):
            raise ConnectionError("postgres unavailable")

    with pytest.raises(ConnectionError, match="postgres unavailable"):
        await migration._plaintext_columns(_FailingConnection())


@pytest.mark.asyncio
async def test_migration_and_close_failures_are_both_reported(monkeypatch) -> None:
    _configure_encryption(monkeypatch)
    connection = _Connection({"api_key"})
    _install_database(monkeypatch, connection, close_error=OSError("close failed"))

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await migration.main()

    messages = [str(error) for error in exc_info.value.exceptions]
    assert any("明文凭据列不完整" in message for message in messages)
    assert "close failed" in messages


def _install_database(
    monkeypatch,
    connection: _Connection,
    *,
    close_error: BaseException | None = None,
) -> list[object]:
    import tortoise.transactions as transaction_module
    from antcode_core.infrastructure.db import tortoise as db_module

    events: list[object] = []

    async def init_db(*, service: str) -> None:
        events.append(("init", service))

    async def close_db() -> None:
        events.append("close")
        if close_error is not None:
            raise close_error

    monkeypatch.setattr(migration, "load_dotenv", lambda _path: None)
    monkeypatch.setattr(db_module, "init_db", init_db)
    monkeypatch.setattr(db_module, "close_db", close_db)
    monkeypatch.setattr(transaction_module, "in_transaction", lambda: _Transaction(connection))
    return events
