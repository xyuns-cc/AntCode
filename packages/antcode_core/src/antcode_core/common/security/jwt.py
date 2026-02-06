"""JWT 认证模块

提供 JWT 令牌的创建、验证和管理功能。
"""

import secrets
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from loguru import logger
from pydantic import BaseModel

from antcode_core.common.config import settings
from antcode_core.common.exceptions import AuthenticationError


class TokenData(BaseModel):
    """令牌数据"""

    user_id: int
    username: str
    exp: datetime


class JWTSecretManager:
    """JWT 密钥管理器"""

    def __init__(self, secret_file: Path | str | None = None):
        if secret_file:
            self.secret_file = Path(secret_file)
        else:
            self.secret_file = Path(settings.data_dir) / ".jwt_secret"
        self._secret: str | None = None

    def get_secret(self) -> str:
        """获取 JWT 密钥"""
        if self._secret:
            return self._secret

        # 尝试从文件加载
        if self.secret_file.exists():
            try:
                if secret := self.secret_file.read_text().strip():
                    self._secret = secret
                    return self._secret
            except Exception as e:
                logger.warning(f"读取JWT密钥失败: {e}")

        # 生成新密钥
        self._secret = secrets.token_hex(64)
        self._save_secret()
        return self._secret

    def _save_secret(self) -> None:
        """保存密钥到文件"""
        try:
            self.secret_file.parent.mkdir(parents=True, exist_ok=True)
            self.secret_file.write_text(self._secret or "")
            self.secret_file.chmod(0o600)
        except Exception as e:
            logger.warning(f"保存JWT密钥失败: {e}")

    def regenerate(self) -> str:
        """重新生成密钥"""
        self._secret = secrets.token_hex(64)
        self._save_secret()
        return self._secret


# 全局密钥管理器实例
jwt_secret_manager = JWTSecretManager()


class JWTAuth:
    """JWT 认证处理器"""

    def __init__(
        self,
        secret_manager: JWTSecretManager | None = None,
        algorithm: str | None = None,
        expire_minutes: int | None = None,
    ):
        self._secret_manager = secret_manager or jwt_secret_manager
        self.algorithm = algorithm or settings.JWT_ALGORITHM
        self.expire_minutes = expire_minutes or settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    def _get_secret(self) -> str:
        return self._secret_manager.get_secret()

    def create_access_token(
        self,
        user_id: int,
        username: str,
        expires_delta: timedelta | None = None,
        extra_claims: dict | None = None,
    ) -> str:
        """创建访问令牌

        Args:
            user_id: 用户 ID
            username: 用户名
            expires_delta: 可选的过期时间增量
            extra_claims: 额外的声明

        Returns:
            JWT 令牌字符串
        """
        expire = datetime.utcnow() + (expires_delta or timedelta(minutes=self.expire_minutes))
        payload = {
            "user_id": user_id,
            "username": username,
            "exp": expire,
        }
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, self._get_secret(), algorithm=self.algorithm)

    def verify_token(self, token: str) -> TokenData:
        """验证令牌

        Args:
            token: JWT 令牌字符串

        Returns:
            TokenData 对象

        Raises:
            AuthenticationError: 令牌无效或过期
        """
        try:
            payload = jwt.decode(token, self._get_secret(), algorithms=[self.algorithm])
            user_id, username = payload.get("user_id"), payload.get("username")
            if not user_id or not username:
                raise AuthenticationError("无效凭证")
            return TokenData(
                user_id=user_id,
                username=username,
                exp=datetime.fromtimestamp(payload["exp"]),
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationError("令牌已过期")
        except (jwt.InvalidTokenError, jwt.DecodeError):
            raise AuthenticationError("无效凭证")

    def decode_token(self, token: str) -> dict:
        """解码令牌（不验证过期）

        Args:
            token: JWT 令牌字符串

        Returns:
            令牌 payload 字典

        Raises:
            AuthenticationError: 令牌无效
        """
        try:
            return jwt.decode(
                token,
                self._get_secret(),
                algorithms=[self.algorithm],
                options={"verify_exp": False},
            )
        except (jwt.InvalidTokenError, jwt.DecodeError):
            raise AuthenticationError("无效凭证")


# 全局 JWT 认证实例
jwt_auth = JWTAuth()


# 便捷函数
def create_access_token(
    user_id: int,
    username: str,
    expires_delta: timedelta | None = None,
) -> str:
    """创建访问令牌"""
    return jwt_auth.create_access_token(user_id, username, expires_delta)


def verify_token(token: str) -> TokenData:
    """验证令牌"""
    return jwt_auth.verify_token(token)


def decode_token(token: str) -> dict:
    """解码令牌"""
    return jwt_auth.decode_token(token)
