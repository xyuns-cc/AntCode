"""
统一认证模块
包含JWT密钥管理、Token生成验证、用户认证等功能
"""
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from loguru import logger

from .config import settings


# ==================== JWT密钥管理 ====================
class JWTSecretManager:
    """JWT密钥管理器"""
    
    def __init__(self, secret_file: str = None):
        """初始化JWT密钥管理器
        
        Args:
            secret_file: JWT密钥文件路径，默认为项目根目录的 .jwt_secret
        """
        if secret_file is None:
            # 默认保存在项目根目录
            project_root = Path(__file__).parent.parent.parent
            secret_file = project_root / ".jwt_secret"
        
        self.secret_file = Path(secret_file)
        self._secret = None
    
    def get_secret(self) -> str:
        """获取JWT密钥，如果不存在则生成"""
        if self._secret is not None:
            return self._secret
        
        # 尝试从文件读取
        if self.secret_file.exists():
            try:
                self._secret = self.secret_file.read_text().strip()
                if self._secret:
                    logger.info(f"✓ JWT密钥已从文件加载: {self.secret_file}")
                    return self._secret
            except Exception as e:
                logger.warning(f"读取JWT密钥文件失败: {e}")
        
        # 生成新密钥
        self._secret = self._generate_secret()
        
        # 保存到文件
        try:
            self.secret_file.parent.mkdir(parents=True, exist_ok=True)
            self.secret_file.write_text(self._secret)
            # 设置文件权限为只有所有者可读写
            self.secret_file.chmod(0o600)
            logger.info(f"✓ JWT密钥已生成并保存: {self.secret_file}")
        except Exception as e:
            logger.error(f"保存JWT密钥失败: {e}")
            logger.warning("JWT密钥将仅在内存中使用，重启后会重新生成")
        
        return self._secret
    
    def _generate_secret(self) -> str:
        """生成随机密钥"""
        # 生成64字节(512位)的随机密钥，转为十六进制字符串
        return secrets.token_hex(64)
    
    def regenerate(self) -> str:
        """重新生成JWT密钥"""
        self._secret = self._generate_secret()
        
        try:
            self.secret_file.write_text(self._secret)
            self.secret_file.chmod(0o600)
            logger.info(f"✓ JWT密钥已重新生成: {self.secret_file}")
        except Exception as e:
            logger.error(f"保存新JWT密钥失败: {e}")
        
        return self._secret


# 全局JWT密钥管理器实例
jwt_secret_manager = JWTSecretManager()


# ==================== JWT认证 ====================
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


# ==================== 认证依赖 ====================
def get_current_user(credentials=Depends(security)):
    """获取当前用户信息"""
    token = credentials.credentials
    return jwt_auth.verify_token(token)


def get_current_user_id(current_user=Depends(get_current_user)):
    """获取当前用户ID"""
    return current_user.user_id


def get_current_user_from_token(token):
    """从JWT令牌直接获取用户信息（用于WebSocket认证）

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
    """异步验证JWT令牌（用于WebSocket认证）
    
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


def get_optional_current_user(credentials=Depends(security)):
    """获取当前用户信息（可选，用于测试环境）"""
    if not credentials:
        return None
    
    token = credentials.credentials
    try:
        return jwt_auth.verify_token(token)
    except HTTPException:
        return None
