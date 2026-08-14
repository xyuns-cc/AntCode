"""Transactional batch updates for system configuration."""

from collections.abc import Sequence

from tortoise.transactions import in_transaction

from antcode_core.common.security.system_config_secrets import (
    SECRET_MASK,
    is_sensitive_config_key,
)
from antcode_core.domain.models.system_config import SystemConfig
from antcode_core.domain.schemas.system_config import SystemConfigBatchItem


def _update_values(item: SystemConfigBatchItem) -> dict[str, object]:
    values = item.model_dump(
        exclude={"config_key", "category", "value_type"},
        exclude_unset=True,
    )
    if is_sensitive_config_key(item.config_key) and values.get("config_value") == SECRET_MASK:
        values.pop("config_value")
    return values


def _create_values(item: SystemConfigBatchItem, modified_by: str) -> dict[str, object]:
    values = item.model_dump()
    if is_sensitive_config_key(item.config_key) and item.config_value == SECRET_MASK:
        raise ValueError(f"敏感配置 {item.config_key} 不能使用掩码值创建")
    values["modified_by"] = modified_by
    return values


async def batch_update_system_configs(
    configs: Sequence[SystemConfigBatchItem],
    modified_by: str,
) -> int:
    keys = [item.config_key for item in configs]
    async with in_transaction() as connection:
        rows = await SystemConfig.filter(config_key__in=keys).using_db(connection).select_for_update()
        existing = {row.config_key: row for row in rows}
        for item in configs:
            config = existing.get(item.config_key)
            if config is None:
                await SystemConfig.create(using_db=connection, **_create_values(item, modified_by))
                continue
            values = _update_values(item)
            values["modified_by"] = modified_by
            await config.update_from_dict(values).save(using_db=connection)
    return len(configs)


__all__ = ["batch_update_system_configs"]
