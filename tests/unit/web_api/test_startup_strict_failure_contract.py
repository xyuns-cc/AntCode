import importlib

import pytest

lifespan_module = importlib.import_module("antcode_web_api.lifespan")


@pytest.mark.asyncio
async def test_system_config_init_failure_stops_startup(monkeypatch):
    from antcode_core.application.services.system_config import system_config_service

    async def fail_init():
        raise RuntimeError("system config failed")

    monkeypatch.setattr(system_config_service, "initialize_default_configs", fail_init)

    with pytest.raises(RuntimeError, match="system config failed"):
        await lifespan_module._init_system_config()


@pytest.mark.asyncio
async def test_worker_auth_init_failure_stops_startup(monkeypatch):
    from antcode_core.application.services.workers.worker_service import worker_service

    async def fail_init():
        raise RuntimeError("worker auth failed")

    monkeypatch.setattr(worker_service, "init_worker_secrets", fail_init)

    with pytest.raises(RuntimeError, match="worker auth failed"):
        await lifespan_module._init_worker_auth()
