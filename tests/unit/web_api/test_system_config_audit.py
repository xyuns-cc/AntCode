from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.schemas.system_config import SystemConfigCreate
from antcode_web_api.routes.v1 import system_config, system_config_audit

HTTP_CREATED = 201


def _admin() -> SimpleNamespace:
    return SimpleNamespace(user_id=7, username="root")


def _stored_config() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        config_key="smtp_password",
        config_value="must-not-enter-audit",
        category="alert",
        description="secret",
        value_type="string",
        is_active=True,
        modified_by="root",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_system_config_audit_records_keys_without_values(monkeypatch) -> None:
    audit_write = AsyncMock()
    monkeypatch.setattr(system_config_audit.audit_service, "log", audit_write)

    await system_config_audit.audit_config_change(
        _admin(),
        operation="system_config_batch_update",
        config_keys=["smtp_password", "webhook_url"],
        description="批量更新系统配置",
        updated_count=2,
    )

    kwargs = audit_write.await_args.kwargs
    assert kwargs["new_value"] == {
        "config_keys": ["smtp_password", "webhook_url"],
        "updated_count": 2,
    }
    assert "must-not-enter-audit" not in str(kwargs)


@pytest.mark.asyncio
async def test_config_create_returns_committed_resource_when_audit_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        system_config.system_config_service,
        "create_config",
        AsyncMock(return_value=_stored_config()),
    )
    monkeypatch.setattr(
        system_config_audit.audit_service,
        "log",
        AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    request = SystemConfigCreate(
        config_key="smtp_password",
        config_value="must-not-enter-audit",
        category="alert",
    )

    response = await system_config.create_config(request, _admin())

    assert response.data.config_key == "smtp_password"
    assert response.code == HTTP_CREATED


@pytest.mark.asyncio
async def test_generic_config_read_redacts_sensitive_values(monkeypatch) -> None:
    monkeypatch.setattr(
        system_config.system_config_service,
        "get_all_configs",
        AsyncMock(return_value=[_stored_config()]),
    )

    response = await system_config.get_all_configs(current_admin=_admin())

    assert response.data[0].config_value == "***REDACTED***"
    assert "must-not-enter-audit" not in str(response.data)
