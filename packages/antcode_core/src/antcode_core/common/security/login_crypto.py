"""登录密码加密/解密工具"""

from __future__ import annotations

import base64
import hashlib
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
        if private_key_path.exists():
            self._private_key = self._load_private_key(private_key_path)
            return self._private_key

        self._private_key = self._generate_private_key()
        self._persist_private_key(private_key_path, self._private_key)
        self._persist_public_key(self._resolve_public_key_path(), self._private_key.public_key())
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
        try:
            key_bytes = path.read_bytes()
            return serialization.load_pem_private_key(key_bytes, password=None)
        except Exception as exc:
            logger.warning(f"读取登录私钥失败: {exc}, 将重新生成密钥")
            private_key = self._generate_private_key()
            self._persist_private_key(path, private_key)
            self._persist_public_key(self._resolve_public_key_path(), private_key.public_key())
            return private_key

    def _generate_private_key(self) -> rsa.RSAPrivateKey:
        return rsa.generate_private_key(
            public_exponent=65537,
            key_size=settings.LOGIN_RSA_KEY_SIZE,
        )

    def _persist_private_key(self, path: Path, private_key: rsa.RSAPrivateKey) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            path.write_bytes(key_bytes)
            path.chmod(0o600)
        except Exception as exc:
            logger.warning(f"保存登录私钥失败: {exc}")

    def _persist_public_key(self, path: Path, public_key: rsa.RSAPublicKey) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key_bytes = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            path.write_bytes(key_bytes)
            try:
                path.chmod(0o644)
            except OSError:
                pass
        except Exception as exc:
            logger.warning(f"保存登录公钥失败: {exc}")


login_password_crypto = LoginPasswordCrypto()

