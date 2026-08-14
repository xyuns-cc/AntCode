"""Tortoise 字段级错误消息安全边界。"""

from __future__ import annotations

from typing import Any

from tortoise import fields
from tortoise.models import Model

from antcode_core.common.error_messages import normalize_persisted_error_message


class PersistedErrorMessageField(fields.TextField):
    """保证所有 ORM create/update/save 路径都执行统一规范化。"""

    def to_db_value(self, value: Any, instance: type[Model] | Model) -> str | None:
        normalized = normalize_persisted_error_message(value)
        return super().to_db_value(normalized, instance)


__all__ = ["PersistedErrorMessageField"]
