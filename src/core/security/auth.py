"""认证模块"""
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from loguru import logger

from src.core.config import settings

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
    """JWT 密钥管理器"""

    def __init__(self, secret_file: Optional[Path] = None):
        self.secret_file = Path(secret_file) if secret_file else Path(settings.data_dir) / ".jwt_secret"
        self._secret: Optional[str] = None

    def get_secret(self) -> str:
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

    def _save_secret(self):
        """保存密钥到文件"""
        try:
            self.secret_file.parent.mkdir(parents=True, exist_ok=True)
            self.secret_file.write_text(self._secret)
            self.secret_file.chmod(0o600)
        except Exception as e:
            logger.warning(f"保存JWT密钥失败: {e}")

    def regenerate(self) -> str:
        """重新生成密钥"""
        self._secret = secrets.token_hex(64)
        self._save_secret()
        return self._secret


jwt_secret_manager = JWTSecretManager()


class TokenData(BaseModel):
    """令牌数据"""
    user_id: int
    username: str
    session_id: str
    token_type: str
    iat: datetime
    exp: datetime


class JWTAuth:
    """JWT 认证处理器"""

    def __init__(self):
        self.algorithm = settings.JWT_ALGORITHM
        self.expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    def _get_secret(self) -> str:
        return settings.JWT_SECRET_KEY

    def create_access_token(
        self,
        user_id: int,
        username: str,
        session_id: str,
        token_type: str = "access",
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """创建访问令牌"""
        if not session_id:
            raise ValueError("缺少 session_id")

        now = datetime.utcnow()
        expire = now + (expires_delta or timedelta(minutes=self.expire_minutes))
        return jwt.encode(
            {
                "user_id": user_id,
                "username": username,
                "sid": session_id,
                "typ": token_type,
                "iat": now,
                "jti": uuid.uuid4().hex,
                "exp": expire,
            },
            self._get_secret(),
            algorithm=self.algorithm
        )

    def verify_token(self, token: str) -> TokenData:
        """验证令牌"""
        try:
            payload = jwt.decode(token, self._get_secret(), algorithms=[self.algorithm])
            user_id = payload.get("user_id")
            username = payload.get("username")
            session_id = payload.get("sid")
            token_type = payload.get("typ")
            iat = payload.get("iat")

            if not user_id or not username or not session_id or not token_type or not iat:
                raise AUTH_ERROR
            return TokenData(
                user_id=user_id,
                username=username,
                session_id=session_id,
                token_type=token_type,
                iat=datetime.fromtimestamp(iat) if isinstance(iat, (int, float)) else iat,
                exp=datetime.fromtimestamp(payload["exp"])
            )
        except jwt.ExpiredSignatureError:
            raise TOKEN_EXPIRED_ERROR
        except (jwt.InvalidTokenError, jwt.DecodeError):
            raise AUTH_ERROR


jwt_auth = JWTAuth()
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


# === 依赖注入函数 ===

async def _authenticate_token(token: str) -> TokenData:
    token_data = jwt_auth.verify_token(token)

    from src.services.sessions.session_service import user_session_service
    from src.services.users.user_service import user_service

    session = await user_session_service.get_session_by_public_id(token_data.session_id)
    if not session or session.revoked_at:
        raise AUTH_ERROR

    if int(session.user_id) != int(token_data.user_id):
        raise AUTH_ERROR

    user = await user_service.get_user_by_id(token_data.user_id)
    if not user or not user.is_active:
        raise AUTH_ERROR

    if session.session_type == "web":
        await user_session_service.touch_session(session)

    return token_data


async def get_current_user(credentials=Depends(security)) -> TokenData:
    """获取当前用户"""
    return await _authenticate_token(credentials.credentials)


def get_current_user_id(current_user: TokenData = Depends(get_current_user)) -> int:
    """获取当前用户ID"""
    return current_user.user_id


async def get_current_user_from_token(token: str) -> TokenData:
    """从令牌获取用户（异步）"""
    return await _authenticate_token(token)


async def verify_token(token: str) -> TokenData:
    """验证令牌（异步）"""
    return await _authenticate_token(token)


async def get_current_admin_user(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """获取当前管理员用户"""
    from src.services.users.user_service import user_service
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


async def get_current_super_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """获取超级管理员（仅 admin 用户）"""
    from src.services.users.user_service import user_service
    from src.core.config import settings
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user or not user.is_admin or user.username != settings.DEFAULT_ADMIN_USERNAME:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要超级管理员权限")
    return current_user


async def verify_super_admin(user: TokenData) -> bool:
    """验证是否为超级管理员"""
    from src.services.users.user_service import user_service
    from src.core.config import settings
    user_obj = await user_service.get_user_by_id(user.user_id)
    return bool(user_obj and user_obj.is_admin and user_obj.username == settings.DEFAULT_ADMIN_USERNAME)


async def get_optional_current_user(credentials=Depends(optional_security)) -> Optional[TokenData]:
    """获取当前用户（可选，不抛异常）"""
    if not credentials:
        return None
    try:
        return await _authenticate_token(credentials.credentials)
    except HTTPException:
        return None
