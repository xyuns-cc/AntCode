from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scripts import encrypt_sensitive_data as migration


class _Row:
    def __init__(self, row_id: int) -> None:
        self.id = row_id
        self.saved_fields: list[list[str]] = []

    async def save(self, *, using_db, update_fields: list[str]) -> None:
        assert using_db is not None
        self.saved_fields.append(update_fields)


class _RowsQuery:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self

    def order_by(self, _field: str):
        return self

    def limit(self, size: int):
        async def result():
            return self.rows[:size]

        return result()


class _RowsModel:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows

    def filter(self, *, id__gt: int):
        return _RowsQuery([row for row in self.rows if row.id > id__gt])


class _TransactionContext:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


@pytest.mark.asyncio
async def test_rewrite_resaves_every_row_with_exact_encrypted_fields() -> None:
    rows = [_Row(1), _Row(2)]
    fields = ["runtime_config", "environment_vars"]

    count = await migration._rewrite(_RowsModel(rows), fields, object())

    assert count == 2
    assert [row.saved_fields for row in rows] == [[fields], [fields]]


@pytest.mark.asyncio
async def test_main_runs_migration_and_closes_database(monkeypatch, capsys) -> None:
    events: list[object] = []

    async def init_db(*, service: str) -> None:
        events.append(("init", service))

    async def close_db() -> None:
        events.append("close")

    migrate = AsyncMock(return_value={"system_configs": 1, "tasks": 1})
    monkeypatch.setattr(migration, "init_db", init_db)
    monkeypatch.setattr(migration, "close_db", close_db)
    monkeypatch.setattr(migration, "migrate_sensitive_data", migrate)

    await migration.main()

    assert events == [("init", "migration"), "close"]
    migrate.assert_awaited_once_with()
    output = capsys.readouterr().out
    assert "'system_configs': 1" in output
    assert "'tasks': 1" in output


@pytest.mark.asyncio
async def test_main_propagates_database_failure_and_still_closes(monkeypatch) -> None:
    close_db = AsyncMock()
    monkeypatch.setattr(migration, "init_db", AsyncMock())
    monkeypatch.setattr(migration, "close_db", close_db)
    monkeypatch.setattr(
        migration,
        "migrate_sensitive_data",
        AsyncMock(side_effect=ConnectionError("database read failed")),
    )

    with pytest.raises(ConnectionError, match="database read failed"):
        await migration.main()

    close_db.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_migration_and_close_failures_are_both_reported(monkeypatch) -> None:
    monkeypatch.setattr(migration, "init_db", AsyncMock())
    monkeypatch.setattr(
        migration,
        "migrate_sensitive_data",
        AsyncMock(side_effect=ConnectionError("database write failed")),
    )
    monkeypatch.setattr(
        migration,
        "close_db",
        AsyncMock(side_effect=OSError("database close failed")),
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await migration.main()

    messages = [str(error) for error in exc_info.value.exceptions]
    assert messages == ["database write failed", "database close failed"]


@pytest.mark.asyncio
async def test_migration_ledger_check_is_parameterized() -> None:
    connection = AsyncMock()
    connection.execute_query_dict.return_value = [{"ok": 1}]

    assert await migration._already_applied(connection) is True
    sql, params = connection.execute_query_dict.await_args.args
    assert "$1" in sql
    assert params == [migration.MIGRATION_NAME]


@pytest.mark.asyncio
async def test_migration_marks_ledger_only_after_rewrite(monkeypatch) -> None:
    setup_connection = AsyncMock()
    transaction = AsyncMock()
    transaction.execute_query_dict.return_value = []
    row = _Row(1)
    monkeypatch.setattr(migration.Tortoise, "get_connection", lambda _name: setup_connection)
    monkeypatch.setattr(migration, "in_transaction", lambda _name: _TransactionContext(transaction))
    monkeypatch.setattr(
        migration,
        "_model_specs",
        lambda: (("tasks", _RowsModel([row]), ["environment_vars"]),),
    )

    counts = await migration.migrate_sensitive_data()

    assert counts == {"tasks": 1}
    setup_connection.execute_query.assert_awaited_once_with(migration._CREATE_LEDGER_SQL)
    assert row.saved_fields == [["environment_vars"]]
    assert "INSERT INTO antcode_data_migrations" in transaction.execute_query.await_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_rewrite_failure_does_not_write_ledger_marker(monkeypatch) -> None:
    setup_connection = AsyncMock()
    transaction = AsyncMock()
    transaction.execute_query_dict.return_value = []
    monkeypatch.setattr(migration.Tortoise, "get_connection", lambda _name: setup_connection)
    monkeypatch.setattr(migration, "in_transaction", lambda _name: _TransactionContext(transaction))
    monkeypatch.setattr(
        migration,
        "_rewrite",
        AsyncMock(side_effect=ConnectionError("database write failed")),
    )
    monkeypatch.setattr(
        migration,
        "_model_specs",
        lambda: (("tasks", object(), ["environment_vars"]),),
    )

    with pytest.raises(ConnectionError, match="database write failed"):
        await migration.migrate_sensitive_data()

    statements = [call.args[0] for call in transaction.execute_query.await_args_list]
    assert not any("INSERT INTO antcode_data_migrations" in statement for statement in statements)
