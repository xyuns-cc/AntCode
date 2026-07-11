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


def _try_decrypt(token: Any) -> str | None:
    """仅当 token 是当前密钥环可解的真密文时返回明文，否则返回 None。

    "已加密" 标记（前缀 / envelope key）是明文可伪造的：用户提交一个以
    ``enc:v1:`` 开头的字符串或 ``{"__antcode_encrypted_v1__": ...}`` 字典时,
    如果只看标记就直通落库,该行读取时 decrypt 必然抛 InvalidToken,整行永久
    不可读（存储型 DoS）。所以直通前必须验证密文真的能解开。
    """
    if not isinstance(token, str):
        return None
    try:
        return secret_box.decrypt(token)
    except Exception:
        return None


class EncryptedTextField(fields.TextField):
    def to_db_value(self, value: Any, instance: type[Model] | Model) -> str | None:
        if value is None:
            return None
        text = str(value)
        if text.startswith(_TEXT_PREFIX) and _try_decrypt(text.removeprefix(_TEXT_PREFIX)) is not None:
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
        if isinstance(value, dict) and set(value) == {_JSON_KEY} and _try_decrypt(value[_JSON_KEY]) is not None:
            return super().to_db_value(value, instance)
        plaintext = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        envelope = {_JSON_KEY: secret_box.encrypt(plaintext)}
        return super().to_db_value(envelope, instance)

    def to_python_value(self, value: Any) -> Any:
        decoded = super().to_python_value(value)
        if not isinstance(decoded, dict) or set(decoded) != {_JSON_KEY}:
            return decoded
        token = decoded[_JSON_KEY]
        if not isinstance(token, str):
            # 我方 envelope 的 token 一定是 str；非 str 只能是历史遗留的
            # 用户明文字典，原样返回而不是 str() 后硬解密报错。
            return decoded
        plaintext = secret_box.decrypt(token)
        return json.loads(plaintext)


__all__ = ["EncryptedJSONField", "EncryptedTextField"]
