"""System configuration batches are strict, atomic, and secret-aware."""

import pytest
import pytest_asyncio
from antcode_core.application.services.system_config.batch_update import batch_update_system_configs
from antcode_core.common.config import settings
from antcode_core.common.security.system_config_secrets import SECRET_MASK
from antcode_core.domain.models.system_config import SystemConfig
from antcode_core.domain.schemas.system_config import SystemConfigBatchItem
from tortoise import Tortoise

TEST_KEY = "system-config-test-encryption-key-material"
TEST_SALT = "system-config-test-salt"


@pytest_asyncio.fixture
async def system_config_database(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", TEST_KEY)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", TEST_SALT)
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", "")
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.system_config"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_masked_secret_update_preserves_stored_value(system_config_database) -> None:
    await SystemConfig.create(
        config_key="smtp_password",
        config_value="stored-secret",
        category="alert",
    )

    await batch_update_system_configs(
        [SystemConfigBatchItem(config_key="smtp_password", config_value=SECRET_MASK)],
        "root",
    )

    config = await SystemConfig.get(config_key="smtp_password")
    assert config.config_value == "stored-secret"


@pytest.mark.asyncio
async def test_batch_failure_rolls_back_preceding_update(system_config_database) -> None:
    await SystemConfig.create(config_key="plain", config_value="before", category="general")
    items = [
        SystemConfigBatchItem(config_key="plain", config_value="after"),
        SystemConfigBatchItem(config_key="api_token", config_value=SECRET_MASK),
    ]

    with pytest.raises(ValueError, match="不能使用掩码值创建"):
        await batch_update_system_configs(items, "root")

    config = await SystemConfig.get(config_key="plain")
    assert config.config_value == "before"
