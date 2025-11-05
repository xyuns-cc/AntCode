"""
JWT认证相关功能
包含token生成、验证、用户身份认证等功能
"""

from datetime import datetime, timedelta

import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from .config import settings


class TokenData(BaseModel):
    """Token数据模型"""
    user_id: int
    username: str
    exp: datetime


class JWTAuth:
    """JWT认证类"""
    
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        
    def create_access_token(self, user_id, username, expires_delta=None):
        """创建访问令牌"""
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
        """验证令牌"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            user_id = payload.get("user_id")
            username = payload.get("username")
            exp = datetime.fromtimestamp(payload.get("exp"))
            
            if user_id is None or username is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无效的认证凭据",
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
                detail="无效的认证凭据",
                headers={"WWW-Authenticate": "Bearer"},
            )


# 创建JWT认证实例
jwt_auth = JWTAuth()

# HTTP Bearer认证方案
security = HTTPBearer()


def get_current_user(credentials=Depends(security)):
    """获取当前用户信息"""
    token = credentials.credentials
    return jwt_auth.verify_token(token)


def get_current_user_id(current_user=Depends(get_current_user)):
    """获取当前用户ID"""
    return current_user.user_id


def get_current_user_from_token(token):
    """
    从JWT令牌直接获取用户信息（用于WebSocket认证）

    Args:
        token: JWT令牌字符串

    Returns:
        TokenData: 用户令牌数据

    Raises:
        HTTPException: 认证失败时抛出异常
    """
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
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def verify_token(token):
    """
    异步验证JWT令牌（用于WebSocket认证）
    
    Args:
        token: JWT令牌字符串
        
    Returns:
        TokenData: 用户令牌数据
        
    Raises:
        HTTPException: 认证失败时抛出异常
    """
    return get_current_user_from_token(token)


async def get_current_admin_user(current_user=Depends(get_current_user)):
    """获取当前管理员用户（仅管理员可访问）"""
    from src.services.users.user_service import user_service

    # 从数据库获取用户信息验证管理员权限
    user = await user_service.get_user_by_id(current_user.user_id)
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    return current_user


# 可选的认证依赖（用于测试环境）
def get_optional_current_user(credentials=Depends(security)):
    """获取当前用户信息（可选）"""
    if not credentials:
        return None
    
    token = credentials.credentials
    try:
        return jwt_auth.verify_token(token)
    except HTTPException:
        return None



