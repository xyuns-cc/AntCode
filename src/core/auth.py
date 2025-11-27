"""认证模块"""
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer
from loguru import logger
from pydantic import BaseModel

from src.core.config import settings


class JWTSecretManager:
    """JWT密钥管理器"""
    
    def __init__(self, secret_file=None):
        if secret_file is None:
            secret_file = Path(settings.data_dir) / ".jwt_secret"
        
        self.secret_file = Path(secret_file)
        self._secret = None
    
    def get_secret(self):
        if self._secret is not None:
            return self._secret
        
        if self.secret_file.exists():
            try:
                self._secret = self.secret_file.read_text().strip()
                if self._secret:
                    logger.info(f"JWT密钥已加载: {self.secret_file}")
                    return self._secret
            except Exception as e:
                logger.warning(f"读取JWT密钥失败: {e}")
        
        self._secret = self._generate_secret()
        
        try:
            self.secret_file.parent.mkdir(parents=True, exist_ok=True)
            self.secret_file.write_text(self._secret)
            self.secret_file.chmod(0o600)
            logger.info(f"JWT密钥已生成: {self.secret_file}")
        except Exception as e:
            logger.error(f"保存JWT密钥失败: {e}")
            logger.warning("JWT密钥将仅存储在内存中")
        
        return self._secret
    
    def _generate_secret(self):
        return secrets.token_hex(64)
    
    def regenerate(self):
        self._secret = self._generate_secret()
        
        try:
            self.secret_file.write_text(self._secret)
            self.secret_file.chmod(0o600)
            logger.info(f"JWT密钥已重新生成: {self.secret_file}")
        except Exception as e:
            logger.error(f"保存新JWT密钥失败: {e}")
        
        return self._secret


jwt_secret_manager = JWTSecretManager()


class TokenData(BaseModel):
    """令牌数据模型"""
    user_id: int
    username: str
    exp: datetime


class JWTAuth:
    """JWT认证处理器"""
    
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        
    def create_access_token(self, user_id, username, expires_delta=None):
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.expire_minutes)
            
        to_encode = {
            "user_id": user_id,
            "username": username,
            "exp": expire
        }
        
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def verify_token(self, token):
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            user_id = payload.get("user_id")
            username = payload.get("username")
            exp = datetime.fromtimestamp(payload.get("exp"))
            
            if user_id is None or username is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无效凭证",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return TokenData(user_id=user_id, username=username, exp=exp)
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="令牌已过期",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except (jwt.InvalidTokenError, jwt.InvalidSignatureError, jwt.DecodeError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效凭证",
                headers={"WWW-Authenticate": "Bearer"},
            )


jwt_auth = JWTAuth()

security = HTTPBearer()


def get_current_user(credentials=Depends(security)):
    token = credentials.credentials
    return jwt_auth.verify_token(token)


def get_current_user_id(current_user=Depends(get_current_user)):
    return current_user.user_id


def get_current_user_from_token(token):
    try:
        return jwt_auth.verify_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.InvalidTokenError, jwt.InvalidSignatureError, jwt.DecodeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def verify_token(token):
    return get_current_user_from_token(token)


async def get_current_admin_user(current_user=Depends(get_current_user)):
    from src.services.users.user_service import user_service
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user


def get_optional_current_user(credentials=Depends(security)):
    if not credentials:
        return None
    
    token = credentials.credentials
    try:
        return jwt_auth.verify_token(token)
    except HTTPException:
        return None
