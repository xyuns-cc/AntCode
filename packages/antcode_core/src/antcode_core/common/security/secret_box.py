"""应用内密文封装。使用独立 ENCRYPTION_KEY 派生 Fernet 密钥。

T7-P2-5: 支持密钥轮换 (MultiFernet)
-----------------------------------
- ``ENCRYPTION_KEY``：**当前**主密钥。所有 encrypt 都用它。
- ``ENCRYPTION_KEYS_LEGACY``：**逗号分隔**的旧密钥列表，只用于解密老数据。
  轮换流程：把旧 ENCRYPTION_KEY 塞进这个 env → 换新 ENCRYPTION_KEY →
  重启服务 → 老密文由 legacy 解密解开，写回时用新 key 重加密（Fernet 自
  带 rotate）→ 一段时间后把 legacy 清空。

老部署（无 legacy 配置）行为不变；MultiFernet 单密钥退化为普通 Fernet。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, MultiFernet

from antcode_core.common.config import settings


class SecretBox:
    """使用独立加密密钥封装短文本 secret（支持密钥轮换）。"""

    def __init__(self) -> None:
        self._cached: MultiFernet | None = None
        self._cache_key: tuple[str, str] | None = None

    def encrypt(self, plaintext: str) -> str:
        return self._multi().encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        return self._multi().decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def rotate(self, ciphertext: str) -> str:
        """把老密钥加密的密文重加密为当前主密钥版本。

        无 legacy 密钥或已经是当前版本时 MultiFernet.rotate 是幂等的。
        用于运维后台批量重加密 job（可选，不做也不影响功能）。
        """
        return self._multi().rotate(ciphertext.encode("utf-8")).decode("utf-8")

    # ------------------------------------------------------------------

    def _derive_fernet_key(self, key_material: str) -> bytes:
        digest = hashlib.sha256(key_material.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def _multi(self) -> MultiFernet:
        primary = (settings.ENCRYPTION_KEY or "").strip()
        legacy = (getattr(settings, "ENCRYPTION_KEYS_LEGACY", "") or "").strip()
        # 缓存命中（key 未变）
        cache_key = (primary, legacy)
        if self._cached is not None and self._cache_key == cache_key:
            return self._cached

        if not primary:
            raise RuntimeError(
                "ENCRYPTION_KEY 未配置。请在 .env 中设置 ENCRYPTION_KEY（任意随机字符串）"
            )
        fernets: list[Fernet] = [Fernet(self._derive_fernet_key(primary))]
        if legacy:
            for k in legacy.split(","):
                k = k.strip()
                if k and k != primary:
                    fernets.append(Fernet(self._derive_fernet_key(k)))
        self._cached = MultiFernet(fernets)
        self._cache_key = cache_key
        return self._cached


secret_box = SecretBox()


__all__ = ["SecretBox", "secret_box"]
