from types import SimpleNamespace

import pytest
from antcode_worker.app.main import Application


@pytest.mark.asyncio
async def test_worker_setup_initializes_database_before_container(monkeypatch):
    events: list[str] = []

    async def init_database(*args, **kwargs):
        events.append("database")

    def create_test_container(config):
        events.append("container")
        return SimpleNamespace(config=config)

    monkeypatch.setattr(
        "antcode_core.infrastructure.db.tortoise.init_db",
        init_database,
    )
    monkeypatch.setattr(
        "antcode_worker.app.main.create_container",
        create_test_container,
    )

    app = Application(SimpleNamespace(grace_period=0))
    await app.setup()

    assert events == ["database", "container"]
