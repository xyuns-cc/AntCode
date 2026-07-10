"""认证模块"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from loguru import logger
from pydantic import BaseModel

from antcode_core.common.config import settings

# 统一的认证错误
AUTH_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="无效凭证",
    headers={"WWW-Authenticate": "Bearer"},
)
TOKEN_EXPIRED_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="令牌已过期",
    headers={"WWW-Authenticate": "Bearer"},
)


class JWTSecretManager:
    """JWT 密钥管理器

    密钥必须通过环境变量 ``JWT_SECRET`` 或 ``JWT_SECRET_FILE``
    (亦可通过 ``settings.JWT_SECRET_FILE`` 配置) 显式提供。
    懒生成已禁用以避免多进程 race + 容器重启时密钥丢失。
    """

    def __init__(self, secret_file: Path | str | None = None):
        resolved: Path | None = Path(secret_file) if secret_file else None
        if resolved is None:
            env_file = os.getenv("JWT_SECRET_FILE") or settings.JWT_SECRET_FILE
            if env_file:
                resolved = Path(env_file)
        self.secret_file: Path | None = resolved
        self._secret: str | None = None

    def get_secret(self) -> str:
        if self._secret:
            return self._secret

        # 1. 优先环境变量
        if env_secret := os.getenv("JWT_SECRET"):
            self._secret = env_secret.strip()
            if self._secret:
                return self._secret

        # 2. 从指定文件加载
        if self.secret_file and self.secret_file.exists():
            try:
                if secret := self.secret_file.read_text().strip():
                    self._secret = secret
                    return self._secret
            except Exception:
                logger.exception("读取 JWT 密钥失败")

        # 3. 没配置即拒绝启动 —— 禁止懒生成
        raise RuntimeError(
            "JWT_SECRET or JWT_SECRET_FILE must be configured; "
            "lazy secret generation is disabled to avoid multi-process race "
            "conditions and container-restart key loss."
        )

    def regenerate(self) -> str:
        """重新生成密钥 —— 已禁用,改为运维侧轮换。"""
        raise RuntimeError(
            "JWTSecretManager.regenerate() is disabled; "
            "rotate JWT_SECRET via deployment configuration instead."
        )


jwt_secret_manager = JWTSecretManager()


class TokenData(BaseModel):
    """令牌数据"""

    user_id: int
    username: str
    is_admin: bool = False
    role: str = "user"
    exp: datetime

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"


class JWTAuth:
    """JWT 认证处理器"""

    def __init__(self):
        self.algorithm = settings.JWT_ALGORITHM
        self.expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        self.refresh_expire_days = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS

    def _get_secret(self) -> str:
        return jwt_secret_manager.get_secret()

    def _create_token(
        self,
        user_id: int,
        username: str,
        expires_delta: timedelta,
        token_type: str,
        extra: dict | None = None,
    ) -> str:
        expire = datetime.utcnow() + expires_delta
        payload = {
            "user_id": user_id,
            "username": username,
            "exp": expire,
            "token_type": token_type,
        }
        if extra:
            payload.update(extra)
        return jwt.encode(payload, self._get_secret(), algorithm=self.algorithm)

    def create_access_token(
        self,
        user_id: int,
        username: str,
        expires_delta: timedelta | None = None,
        is_admin: bool = False,
        role: str = "user",
    ) -> str:
        """创建访问令牌"""
        extra = {"is_admin": is_admin, "role": role}
        return self._create_token(
            user_id=user_id,
            username=username,
            expires_delta=expires_delta or timedelta(minutes=self.expire_minutes),
            token_type="access",
            extra=extra,
        )

    def create_refresh_token(
        self, user_id: int, username: str, expires_delta: timedelta | None = None
    ) -> str:
        """创建刷新令牌"""
        return self._create_token(
            user_id=user_id,
            username=username,
            expires_delta=expires_delta or timedelta(days=self.refresh_expire_days),
            token_type="refresh",
        )

    def create_action_token(
        self,
        user_id: int,
        username: str,
        token_type: str,
        expires_delta: timedelta,
    ) -> str:
        """创建一次性操作令牌（如邮箱验证/重置密码）"""
        return self._create_token(
            user_id=user_id,
            username=username,
            expires_delta=expires_delta,
            token_type=token_type,
        )

    def verify_token(
        self,
        token: str,
        expected_type: str | None = "access",
        expected_class: str | None = "web",
    ) -> TokenData:
        """验证令牌

        默认 ``verify_exp=True``、``verify_type=True``、``verify_class=True``。
        调用方若需放宽必须显式传 ``expected_type=None`` / ``expected_class=None``,
        **不允许跳过过期校验**。

        P0-a1: token_class 用于隔离 Web 用户会话(``web``)与 Worker 凭据(``worker``),
        Gateway 侧强制 ``expected_class="worker"``,防止普通 Web access JWT 冒充 Worker。
        为向后兼容,payload 里没有 ``token_class`` 字段时视为 ``"web"``。
        """
        try:
            # 显式强制启用过期校验,杜绝 verify_exp=False 旁路
            payload = jwt.decode(
                token,
                self._get_secret(),
                algorithms=[self.algorithm],
                options={"verify_exp": True, "require": ["exp"]},
            )
            token_type = payload.get("token_type")
            if expected_type:
                if token_type:
                    if token_type != expected_type:
                        raise AUTH_ERROR
                elif expected_type != "access":
                    raise AUTH_ERROR

            # P0-a1: 强制校验 token_class,隔离 web / worker 凭据。
            # payload 无 token_class 时向后兼容为 "web"。
            if expected_class:
                actual_class = payload.get("token_class", "web")
                if actual_class != expected_class:
                    raise AUTH_ERROR

            # Worker token 走独立分支:worker_id 从 payload 取,不使用 user_id/username
            if payload.get("token_class") == "worker":
                worker_id = payload.get("worker_id") or payload.get("sub")
                if not worker_id:
                    raise AUTH_ERROR
                return TokenData(
                    user_id=0,  # worker token 不对应 DB user_id
                    username=str(worker_id),
                    is_admin=False,
                    role="worker",
                    exp=datetime.fromtimestamp(payload["exp"]),
                )

            # 常规 Web token 分支
            user_id, username = payload.get("user_id"), payload.get("username")
            if not user_id or not username:
                raise AUTH_ERROR
            return TokenData(
                user_id=user_id,
                username=username,
                is_admin=payload.get("is_admin", False),
                role=payload.get("role", "admin" if payload.get("is_admin") else "user"),
                exp=datetime.fromtimestamp(payload["exp"]),
            )
        except jwt.ExpiredSignatureError:
            raise TOKEN_EXPIRED_ERROR
        except (jwt.InvalidTokenError, jwt.DecodeError):
            raise AUTH_ERROR

    def create_worker_token(
        self,
        worker_id: str,
        expires_delta: timedelta | None = None,
    ) -> str:
        """P0-a1: 签发专用于 Worker <-> Gateway 认证的 JWT。

        payload 里 ``token_class="worker"`` + ``worker_id=...``,不携带用户身份信息;
        Gateway ``_authenticate_jwt`` 强制要求 ``expected_class="worker"``,
        拒绝任何 Web access token 冒充 Worker。
        """
        expire = datetime.utcnow() + (expires_delta or timedelta(days=90))
        payload = {
            "sub": worker_id,
            "worker_id": worker_id,
            "token_class": "worker",
            "token_type": "access",
            "exp": expire,
        }
        return jwt.encode(payload, self._get_secret(), algorithm=self.algorithm)


jwt_auth = JWTAuth()
security = HTTPBearer()


# === 依赖注入函数 ===


def get_current_user(credentials=Depends(security)) -> TokenData:
    """获取当前用户"""
    return jwt_auth.verify_token(credentials.credentials)


def get_current_user_id(current_user: TokenData = Depends(get_current_user)) -> int:
    """获取当前用户ID"""
    return current_user.user_id


def get_current_user_from_token(token: str) -> TokenData:
    """从令牌获取用户（同步）"""
    return jwt_auth.verify_token(token)


def verify_refresh_token(token: str) -> TokenData:
    """验证刷新令牌（同步）"""
    return jwt_auth.verify_token(token, expected_type="refresh")


async def verify_token(token: str) -> TokenData:
    """验证令牌（异步）"""
    return jwt_auth.verify_token(token)


async def get_current_admin_user(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """获取当前管理员用户"""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


async def get_current_super_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """获取超级管理员"""
    if not current_user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要超级管理员权限")
    return current_user


async def verify_super_admin(user: TokenData) -> bool:
    """验证是否为超级管理员"""
    return user.is_super_admin


def get_optional_current_user(credentials=Depends(security)) -> TokenData | None:
    """获取当前用户（可选，不抛异常）"""
    if not credentials:
        return None
    try:
        return jwt_auth.verify_token(credentials.credentials)
    except HTTPException:
        return None
