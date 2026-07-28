from __future__ import annotations

import pytest

from scripts import encrypt_sensitive_data as migration


class _Row:
    def __init__(self) -> None:
        self.saved_fields: list[list[str]] = []

    async def save(self, *, update_fields: list[str]) -> None:
        self.saved_fields.append(update_fields)


class _RowsModel:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows

    async def all(self) -> list[_Row]:
        return self.rows


class _FailingModel:
    async def all(self) -> list[_Row]:
        raise ConnectionError("database read failed")


@pytest.mark.asyncio
async def test_rewrite_resaves_every_row_with_exact_encrypted_fields() -> None:
    rows = [_Row(), _Row()]
    fields = ["runtime_config", "environment_vars"]

    count = await migration._rewrite(_RowsModel(rows), fields)

    assert count == 2
    assert [row.saved_fields for row in rows] == [[fields], [fields]]


@pytest.mark.asyncio
async def test_main_rewrites_all_sensitive_models_and_closes_database(monkeypatch, capsys) -> None:
    events: list[object] = []
    models = {
        "SystemConfig": (_RowsModel([_Row()]), ["config_value"]),
        "ProjectFile": (_RowsModel([_Row()]), ["runtime_config", "environment_vars"]),
        "ProjectCode": (_RowsModel([_Row()]), ["runtime_config", "environment_vars"]),
        "ProjectRule": (_RowsModel([_Row()]), ["headers", "cookies", "proxy_config", "task_config"]),
        "Task": (_RowsModel([_Row()]), ["execution_params", "environment_vars"]),
    }

    async def init_db(*, service: str) -> None:
        events.append(("init", service))

    async def close_db() -> None:
        events.append("close")

    monkeypatch.setattr(migration, "init_db", init_db)
    monkeypatch.setattr(migration, "close_db", close_db)
    for name, (model, _fields) in models.items():
        monkeypatch.setattr(migration, name, model)

    await migration.main()

    assert events == [("init", "migration"), "close"]
    for model, fields in models.values():
        assert model.rows[0].saved_fields == [fields]
    output = capsys.readouterr().out
    assert "'system_configs': 1" in output
    assert "'tasks': 1" in output


@pytest.mark.asyncio
async def test_main_propagates_database_failure_and_still_closes(monkeypatch) -> None:
    closed = False

    async def init_db(*, service: str) -> None:
        assert service == "migration"

    async def close_db() -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(migration, "init_db", init_db)
    monkeypatch.setattr(migration, "close_db", close_db)
    monkeypatch.setattr(migration, "SystemConfig", _FailingModel())

    with pytest.raises(ConnectionError, match="database read failed"):
        await migration.main()

    assert closed is True
