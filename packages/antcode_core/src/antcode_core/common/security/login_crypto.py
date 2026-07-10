"""登录密码加密/解密工具"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from loguru import logger

from antcode_core.common.config import settings

LOGIN_ENCRYPTION_ALGORITHM = "RSA-OAEP-256"


class LoginPasswordCryptoError(ValueError):
    """登录密码加密/解密错误"""


class LoginPasswordCrypto:
    """登录密码加密/解密管理器"""

    def __init__(self) -> None:
        self._private_key: rsa.RSAPrivateKey | None = None
        self._public_key_pem: str | None = None
        self._key_id: str | None = None

    def public_key_payload(self) -> dict[str, str]:
        """获取登录公钥信息"""
        public_key_pem = self._get_public_key_pem()
        return {
            "algorithm": LOGIN_ENCRYPTION_ALGORITHM,
            "key_id": self._get_key_id(),
            "public_key": public_key_pem,
        }

    def decrypt_password(
        self,
        encrypted_password: str,
        algorithm: str | None = None,
        key_id: str | None = None,
    ) -> str:
        """解密登录密码"""
        if algorithm and algorithm != LOGIN_ENCRYPTION_ALGORITHM:
            raise LoginPasswordCryptoError("不支持的加密算法")
        if key_id and key_id != self._get_key_id():
            raise LoginPasswordCryptoError("密钥已过期，请刷新登录页面")

        try:
            cipher_bytes = base64.b64decode(encrypted_password)
        except Exception as exc:  # pragma: no cover - 保底防护
            raise LoginPasswordCryptoError("密码密文格式错误") from exc

        private_key = self._get_private_key()
        try:
            plaintext = private_key.decrypt(
                cipher_bytes,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
        except Exception as exc:
            logger.warning(f"登录密码解密失败: {exc}")
            raise LoginPasswordCryptoError("密码解密失败") from exc

        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LoginPasswordCryptoError("密码解密失败") from exc

    def _get_private_key(self) -> rsa.RSAPrivateKey:
        if self._private_key is not None:
            return self._private_key

        private_key_path = self._resolve_private_key_path()
        # P2-17: 统一走 _load_private_key —— FileNotFoundError 才生成,
        # 其他错误(损坏/权限/共享卷抖动)必须 raise,禁止静默覆盖。
        self._private_key = self._load_private_key(private_key_path)
        return self._private_key

    def _get_public_key_pem(self) -> str:
        if self._public_key_pem is not None:
            return self._public_key_pem

        private_key = self._get_private_key()
        public_key = private_key.public_key()
        pem_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._public_key_pem = pem_bytes.decode("ascii")
        return self._public_key_pem

    def _get_key_id(self) -> str:
        if self._key_id is not None:
            return self._key_id

        if settings.LOGIN_RSA_KEY_ID:
            self._key_id = settings.LOGIN_RSA_KEY_ID
            return self._key_id

        private_key = self._get_private_key()
        public_key = private_key.public_key()
        public_der = public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._key_id = hashlib.sha256(public_der).hexdigest()[:16]
        return self._key_id

    def _resolve_private_key_path(self) -> Path:
        if settings.LOGIN_RSA_PRIVATE_KEY_FILE:
            return Path(settings.LOGIN_RSA_PRIVATE_KEY_FILE).expanduser()
        return Path(settings.data_dir) / "keys" / "login_rsa_private.pem"

    def _resolve_public_key_path(self) -> Path:
        if settings.LOGIN_RSA_PUBLIC_KEY_FILE:
            return Path(settings.LOGIN_RSA_PUBLIC_KEY_FILE).expanduser()
        return Path(settings.data_dir) / "keys" / "login_rsa_public.pem"

    def _load_private_key(self, path: Path) -> rsa.RSAPrivateKey:
        """P2-17: 读取登录私钥。

        - FileNotFoundError(首次启动/未落盘) → 生成并原子落盘
        - 读取或解析失败(损坏 / 权限 / 共享卷抖动 / 半写) → **raise**,
          阻断启动,禁止重新生成覆盖(旧密钥丢失会永久破坏 grace 期内所有
          已发出登录 payload 的解密路径)
        """
        try:
            key_bytes = path.read_bytes()
        except FileNotFoundError:
            logger.info(f"登录私钥不存在,首次生成: {path}")
            private_key = self._generate_private_key()
            self._persist_private_key_atomic(path, private_key)
            self._persist_public_key_atomic(
                self._resolve_public_key_path(), private_key.public_key()
            )
            return private_key
        except OSError as exc:
            raise RuntimeError(
                f"登录私钥读取失败,拒绝重新生成覆盖"
                f"(可能是共享卷抖动/权限问题): {path}, {exc}"
            ) from exc

        try:
            return serialization.load_pem_private_key(key_bytes, password=None)
        except Exception as exc:
            raise RuntimeError(
                f"登录私钥解析失败,拒绝重新生成覆盖"
                f"(可能被截断/损坏,继续会永久丢失旧密钥): {path}, {exc}"
            ) from exc

    def _generate_private_key(self) -> rsa.RSAPrivateKey:
        return rsa.generate_private_key(
            public_exponent=65537,
            key_size=settings.LOGIN_RSA_KEY_SIZE,
        )

    def _persist_private_key_atomic(self, path: Path, private_key: rsa.RSAPrivateKey) -> None:
        """P2-17: 原子写入登录私钥(tmp + rename),失败必须 raise。

        rename 在同一文件系统内是原子的;避免部分写入产生"看似存在但解析失败"
        的坏文件,又被后续启动误判成损坏而(在旧逻辑下)覆盖。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        key_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_bytes(key_bytes)
            try:
                tmp.chmod(0o600)
            except OSError:
                # Windows / 特殊文件系统不支持 chmod —— 记录但不阻断
                logger.warning(f"设置登录私钥权限失败(跳过): {tmp}")
            os.replace(tmp, path)
        except Exception:
            # 清理半写的临时文件
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def _persist_public_key_atomic(self, path: Path, public_key: rsa.RSAPublicKey) -> None:
        """P2-17: 原子写入登录公钥。公钥丢失可重新导出,失败仅 warning。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key_bytes = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(key_bytes)
            try:
                tmp.chmod(0o644)
            except OSError:
                pass
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning(f"保存登录公钥失败: {exc}")
            # 尽力清理 tmp
            try:
                path.with_suffix(path.suffix + ".tmp").unlink(missing_ok=True)
            except Exception:
                pass


login_password_crypto = LoginPasswordCrypto()
