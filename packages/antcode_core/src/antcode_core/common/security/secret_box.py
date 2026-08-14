"""应用内密文封装。使用独立 ENCRYPTION_KEY 派生 Fernet 密钥。

T7-P2-5: 支持密钥轮换 (MultiFernet)
-----------------------------------
- ``ENCRYPTION_KEY``：**当前**主密钥。所有 encrypt 都用它。
- ``ENCRYPTION_KEYS_LEGACY``：**逗号分隔**的旧密钥列表，只用于解密老数据。
  轮换流程：把旧 ENCRYPTION_KEY 塞进这个 env → 换新 ENCRYPTION_KEY →
  重启服务 → 老密文由 legacy 解密解开，写回时用新 key 重加密（Fernet 自
  带 rotate）→ 一段时间后把 legacy 清空。

旧 SHA256 派生只在显式迁移开关开启时加入 keyring，迁移完成后必须关闭。

P2-17: KDF 加固
---------------
旧派生 ``base64(sha256(key))`` 无盐单轮,低熵 ``ENCRYPTION_KEY`` (常见运维随机
串)可被离线爆破。加固为:
- 优先识别 **Fernet 原生 key**(44 字节 urlsafe-base64 编码后是 32 字节): 直接使用。
- 否则走 **PBKDF2-HMAC-SHA256, 100k 迭代**，并强制要求显式、每部署唯一的
  ``ENCRYPTION_KEY_SALT``（至少 16 字节），不再使用全局固定盐。

向后兼容由 ``ENCRYPTION_ALLOW_LEGACY_SHA256`` 显式控制。开启时旧 SHA256 key
只用于 decrypt；执行 rotate 后关闭开关，避免弱派生 key 永久驻留 keyring。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from antcode_core.common.config import settings

_PBKDF2_ITERATIONS = 100_000
_MIN_SALT_BYTES = 16


class SecretBox:
    """使用独立加密密钥封装短文本 secret（支持密钥轮换）。"""

    def __init__(self) -> None:
        self._cached: MultiFernet | None = None
        # 缓存 key 也要包含 salt 变化,避免 env 改 salt 后仍返回旧实例
        self._cache_key: tuple[str, str, str, str, bool] | None = None

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

    def decrypt_primary(self, ciphertext: str) -> str:
        """仅使用当前主密钥解密，用于轮换完成后的独立验证。"""
        return self._primary().decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def needs_rotation(self, ciphertext: str) -> bool:
        """确认密文可由 keyring 解开，并判断它是否仍依赖旧密钥。"""
        self.decrypt(ciphertext)
        try:
            self.decrypt_primary(ciphertext)
        except InvalidToken:
            return True
        return False

    # ------------------------------------------------------------------

    def _derive_fernet_key(self, key_material: str, salt: str) -> bytes:
        """P2-17: 若已是 Fernet 原生 key 就直接用;否则 PBKDF2 派生。"""
        # 尝试识别 Fernet 原生 key(urlsafe-base64 编码后 32 字节)
        try:
            decoded = base64.urlsafe_b64decode(key_material.encode("utf-8"))
            if len(decoded) == 32:
                return key_material.encode("utf-8")
        except (ValueError, TypeError):
            pass
        if len(key_material.encode("utf-8")) < 32:
            raise RuntimeError("ENCRYPTION_KEY 必须至少包含 32 字节高熵材料")
        if len(salt.encode("utf-8")) < _MIN_SALT_BYTES:
            raise RuntimeError("ENCRYPTION_KEY_SALT 必须显式配置且至少包含 16 字节")
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.encode("utf-8"),
            iterations=_PBKDF2_ITERATIONS,
        )
        return base64.urlsafe_b64encode(kdf.derive(key_material.encode("utf-8")))

    def _derive_legacy_sha256_key(self, key_material: str) -> bytes:
        """旧派生方式,仅用于 decrypt 老密文(dual-decrypt 向后兼容)。"""
        digest = hashlib.sha256(key_material.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def _primary(self) -> Fernet:
        primary = (settings.ENCRYPTION_KEY or "").strip()
        salt = (getattr(settings, "ENCRYPTION_KEY_SALT", "") or "").strip()
        if not primary:
            raise RuntimeError("ENCRYPTION_KEY 未配置。请设置至少 32 字节的高熵密钥")
        return Fernet(self._derive_fernet_key(primary, salt))

    def _multi(self) -> MultiFernet:
        primary = (settings.ENCRYPTION_KEY or "").strip()
        legacy = (getattr(settings, "ENCRYPTION_KEYS_LEGACY", "") or "").strip()
        salt = (getattr(settings, "ENCRYPTION_KEY_SALT", "") or "").strip()
        legacy_kdf_salt = (getattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "") or "").strip()
        allow_legacy_sha256 = bool(getattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False))
        # 缓存命中（primary/legacy/salt 三者未变）
        cache_key = (primary, legacy, salt, legacy_kdf_salt, allow_legacy_sha256)
        if self._cached is not None and self._cache_key == cache_key:
            return self._cached

        if not primary:
            raise RuntimeError("ENCRYPTION_KEY 未配置。请设置至少 32 字节的高熵密钥")

        fernets: list[Fernet] = [self._primary()]
        if legacy_kdf_salt and legacy_kdf_salt != salt:
            fernets.append(Fernet(self._derive_fernet_key(primary, legacy_kdf_salt)))
        if allow_legacy_sha256:
            fernets.append(Fernet(self._derive_legacy_sha256_key(primary)))
        if legacy:
            seen: set[str] = {primary}
            for k in legacy.split(","):
                k = k.strip()
                if not k or k in seen:
                    continue
                seen.add(k)
                fernets.append(Fernet(self._derive_fernet_key(k, salt)))
                if legacy_kdf_salt and legacy_kdf_salt != salt:
                    fernets.append(Fernet(self._derive_fernet_key(k, legacy_kdf_salt)))
                if allow_legacy_sha256:
                    fernets.append(Fernet(self._derive_legacy_sha256_key(k)))
        self._cached = MultiFernet(fernets)
        self._cache_key = cache_key
        return self._cached


secret_box = SecretBox()


__all__ = ["SecretBox", "secret_box"]
