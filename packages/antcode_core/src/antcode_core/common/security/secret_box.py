"""应用内密文封装。使用独立 ENCRYPTION_KEY 派生 Fernet 密钥。

T7-P2-5: 支持密钥轮换 (MultiFernet)
-----------------------------------
- ``ENCRYPTION_KEY``：**当前**主密钥。所有 encrypt 都用它。
- ``ENCRYPTION_KEYS_LEGACY``：**逗号分隔**的旧密钥列表，只用于解密老数据。
  轮换流程：把旧 ENCRYPTION_KEY 塞进这个 env → 换新 ENCRYPTION_KEY →
  重启服务 → 老密文由 legacy 解密解开，写回时用新 key 重加密（Fernet 自
  带 rotate）→ 一段时间后把 legacy 清空。

老部署（无 legacy 配置）行为不变；MultiFernet 单密钥退化为普通 Fernet。

P2-17: KDF 加固
---------------
旧派生 ``base64(sha256(key))`` 无盐单轮,低熵 ``ENCRYPTION_KEY`` (常见运维随机
串)可被离线爆破。加固为:
- 优先识别 **Fernet 原生 key**(44 字节 urlsafe-base64 编码后是 32 字节): 直接使用。
- 否则走 **PBKDF2-HMAC-SHA256, 100k 迭代**,盐取自 ``ENCRYPTION_KEY_SALT``
  env(默认 ``antcode-fernet-v1``)。相同 salt+key 派生结果稳定,可复现。

向后兼容(dual-decrypt):MultiFernet 的密钥列表同时包含
``[PBKDF2(primary), SHA256(primary), PBKDF2(legacy_i)..., SHA256(legacy_i)...]``。
第一个 key(PBKDF2 派生)是 encrypt/rotate 的写入版本;后续 key 只用于 decrypt,
让旧 SHA256 密文可以继续解开,rotate 后自然升级到 PBKDF2 版本。
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from antcode_core.common.config import settings

# P2-17: 默认盐固定,让相同 ENCRYPTION_KEY 派生稳定;运维可通过
# ENCRYPTION_KEY_SALT 覆盖(改 salt 需要重加密,类同轮换)。
_DEFAULT_KDF_SALT = "antcode-fernet-v1"
_PBKDF2_ITERATIONS = 100_000


class SecretBox:
    """使用独立加密密钥封装短文本 secret（支持密钥轮换）。"""

    def __init__(self) -> None:
        self._cached: MultiFernet | None = None
        # 缓存 key 也要包含 salt 变化,避免 env 改 salt 后仍返回旧实例
        self._cache_key: tuple[str, str, str] | None = None

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

    def _derive_fernet_key(self, key_material: str, salt: str) -> bytes:
        """P2-17: 若已是 Fernet 原生 key 就直接用;否则 PBKDF2 派生。"""
        # 尝试识别 Fernet 原生 key(urlsafe-base64 编码后 32 字节)
        try:
            decoded = base64.urlsafe_b64decode(key_material.encode("utf-8"))
            if len(decoded) == 32:
                return key_material.encode("utf-8")
        except Exception:
            pass
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

    def _multi(self) -> MultiFernet:
        primary = (settings.ENCRYPTION_KEY or "").strip()
        legacy = (getattr(settings, "ENCRYPTION_KEYS_LEGACY", "") or "").strip()
        salt = (os.getenv("ENCRYPTION_KEY_SALT") or _DEFAULT_KDF_SALT).strip() or _DEFAULT_KDF_SALT
        # 缓存命中（primary/legacy/salt 三者未变）
        cache_key = (primary, legacy, salt)
        if self._cached is not None and self._cache_key == cache_key:
            return self._cached

        if not primary:
            raise RuntimeError(
                "ENCRYPTION_KEY 未配置。请在 .env 中设置 ENCRYPTION_KEY（任意随机字符串）"
            )

        # dual-decrypt:第一个 key 是加固后的 PBKDF2 派生(encrypt/rotate 会写入这个);
        # 其后保留同一 primary 的旧 SHA256 派生,让升级前的密文能继续解开。
        fernets: list[Fernet] = [
            Fernet(self._derive_fernet_key(primary, salt)),
            Fernet(self._derive_legacy_sha256_key(primary)),
        ]
        if legacy:
            seen: set[str] = {primary}
            for k in legacy.split(","):
                k = k.strip()
                if not k or k in seen:
                    continue
                seen.add(k)
                # legacy 也同时挂 PBKDF2 + 旧 SHA256 版本,兼容"轮换前后各时期"的密文
                fernets.append(Fernet(self._derive_fernet_key(k, salt)))
                fernets.append(Fernet(self._derive_legacy_sha256_key(k)))
        self._cached = MultiFernet(fernets)
        self._cache_key = cache_key
        return self._cached


secret_box = SecretBox()


__all__ = ["SecretBox", "secret_box"]
