import importlib

import pytest
from antcode_web_api.services import worker_installer

lifespan_module = importlib.import_module("antcode_web_api.lifespan")


def test_required_worker_install_config_failure_stops_startup(monkeypatch):
    monkeypatch.setattr(lifespan_module.settings, "WORKER_INSTALL_CONFIG_REQUIRED", True)

    def fail_config(_settings):
        raise worker_installer.WorkerInstallerConfigurationError("invalid installer config")

    monkeypatch.setattr(worker_installer, "load_worker_install_config", fail_config)

    with pytest.raises(worker_installer.WorkerInstallerConfigurationError, match="invalid installer config"):
        lifespan_module.validate_required_worker_install_config(lifespan_module.settings)


def test_optional_worker_install_config_is_not_validated(monkeypatch):
    monkeypatch.setattr(lifespan_module.settings, "WORKER_INSTALL_CONFIG_REQUIRED", False)

    def fail_if_called(_settings):
        raise AssertionError("loader must not be called")

    monkeypatch.setattr(worker_installer, "load_worker_install_config", fail_if_called)
    lifespan_module.validate_required_worker_install_config(lifespan_module.settings)


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
