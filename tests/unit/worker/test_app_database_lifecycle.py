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


@pytest.mark.asyncio
async def test_backendless_gateway_worker_skips_database(monkeypatch):
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
    monkeypatch.setattr(
        "antcode_core.common.config.settings.WORKER_GATEWAY_BACKENDLESS",
        True,
    )

    app = Application(SimpleNamespace(grace_period=0, transport_mode="gateway"))
    await app.setup()
    await app._close_database()

    assert events == ["container"]
    assert app._database_initialized is False
