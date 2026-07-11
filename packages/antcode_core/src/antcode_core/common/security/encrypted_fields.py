"""Tortoise 透明加密字段，保持现有 TEXT/JSONB 数据库列类型。"""

from __future__ import annotations

import json
from typing import Any, Generic, TypeVar

from tortoise import fields
from tortoise.models import Model

from antcode_core.common.security.secret_box import secret_box

T = TypeVar("T")
_TEXT_PREFIX = "enc:v1:"
_JSON_KEY = "__antcode_encrypted_v1__"


class EncryptedTextField(fields.TextField):
    def to_db_value(self, value: Any, instance: type[Model] | Model) -> str | None:
        if value is None:
            return None
        text = str(value)
        if text.startswith(_TEXT_PREFIX):
            return text
        return _TEXT_PREFIX + secret_box.encrypt(text)

    def to_python_value(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        if not text.startswith(_TEXT_PREFIX):
            return text
        return secret_box.decrypt(text.removeprefix(_TEXT_PREFIX))


class EncryptedJSONField(fields.JSONField[T], Generic[T]):
    def to_db_value(self, value: Any, instance: type[Model] | Model) -> str | None:
        if value is None:
            return None
        if isinstance(value, dict) and set(value) == {_JSON_KEY}:
            return super().to_db_value(value, instance)
        plaintext = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        envelope = {_JSON_KEY: secret_box.encrypt(plaintext)}
        return super().to_db_value(envelope, instance)

    def to_python_value(self, value: Any) -> Any:
        decoded = super().to_python_value(value)
        if not isinstance(decoded, dict) or set(decoded) != {_JSON_KEY}:
            return decoded
        plaintext = secret_box.decrypt(str(decoded[_JSON_KEY]))
        return json.loads(plaintext)


__all__ = ["EncryptedJSONField", "EncryptedTextField"]
