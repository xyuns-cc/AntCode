"""应用内密文封装。使用独立 ENCRYPTION_KEY 派生 Fernet 密钥。"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from antcode_core.common.config import settings


class SecretBox:
    """使用独立加密密钥封装短文本 secret。"""

    def encrypt(self, plaintext: str) -> str:
        return self._fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def _fernet(self) -> Fernet:
        key_material = settings.ENCRYPTION_KEY
        if not key_material:
            raise RuntimeError(
                "ENCRYPTION_KEY 未配置。请在 .env 中设置 ENCRYPTION_KEY（任意随机字符串）"
            )
        digest = hashlib.sha256(key_material.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
        return Fernet(key)


secret_box = SecretBox()


__all__ = ["SecretBox", "secret_box"]
