from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.alert.alert_service import AlertService

CONFIG_RELOAD_COUNT = 2


def _config(*, levels: list[str]) -> dict:
    config = AlertService._default_config()
    config["auto_alert_levels"] = levels
    return config


@pytest.mark.asyncio
async def test_send_alert_reloads_authoritative_config_and_uses_auto_levels(monkeypatch) -> None:
    service = AlertService()
    service._load_config_from_db = AsyncMock(
        side_effect=[
            _config(levels=["ERROR"]),
            _config(levels=["CRITICAL"]),
        ]
    )
    apply_config = AsyncMock()
    service._apply_config = apply_config
    send_auto = MagicMock(return_value={"status": "queued"})
    module = importlib.import_module("antcode_core.application.services.alert.alert_service")
    monkeypatch.setattr(module.alert_manager, "send_alert_auto", send_auto)

    await service.send_alert("first", level="ERROR")
    await service.send_alert("second", level="CRITICAL")

    assert apply_config.await_count == CONFIG_RELOAD_COUNT
    assert send_auto.call_args_list[0].args[2] == ["ERROR"]
    assert send_auto.call_args_list[1].args[2] == ["CRITICAL"]


@pytest.mark.asyncio
async def test_send_alert_does_not_hide_database_config_failure() -> None:
    service = AlertService()
    service._load_config_from_db = AsyncMock(side_effect=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.send_alert("must fail")


@pytest.mark.asyncio
async def test_unchanged_config_does_not_rebuild_channels() -> None:
    service = AlertService()
    config = _config(levels=["ERROR"])
    service._load_config_from_db = AsyncMock(return_value=config)
    service._apply_config = AsyncMock()

    await service.reload_config()
    await service.reload_config()

    service._apply_config.assert_awaited_once_with(config)
