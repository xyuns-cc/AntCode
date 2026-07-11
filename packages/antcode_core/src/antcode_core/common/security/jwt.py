"""JWT 认证模块(thin re-export)

历史上本模块和 ``auth.py`` 各自维护了一份 ``JWTSecretManager`` /
``JWTAuth`` / ``decode_token``,且 ``jwt.py`` 的 ``decode_token``
默认 ``verify_exp=False`` —— Gateway 走这条路径时令牌过期不会被拒,
属于鉴权绕过(P0-#3)。

现在统一以 ``auth.py`` 为单一实现源,本模块只做 re-export,
``decode_token`` 改为 thin wrapper 走 ``verify_token`` 强制校验过期。
"""

from antcode_core.common.security.auth import (
    JWTAuth,
    JWTSecretManager,
    TokenData,
    jwt_auth,
    jwt_secret_manager,
)


def create_access_token(user_id: int, username: str, expires_delta=None) -> str:
    """创建访问令牌(向后兼容入口)。"""
    return jwt_auth.create_access_token(
        user_id,
        username,
        expires_delta=expires_delta,
    )


def verify_token(token: str) -> TokenData:
    """验证访问令牌 —— 默认强制 verify_exp=True、verify_type='access'。"""
    return jwt_auth.verify_token(token, expected_type="access")


def decode_token(token: str) -> dict:
    """解码令牌(已不再支持跳过过期校验)。

    向后兼容旧调用方:Gateway 等下游历史上调 ``decode_token`` 期望拿到
    ``dict`` payload。这里走标准 ``verify_token``,过期/无效令牌一律
    抛 ``HTTPException(401)``。
    """
    token_data = jwt_auth.verify_token(token, expected_type=None)
    return token_data.model_dump()


__all__ = [
    "JWTAuth",
    "JWTSecretManager",
    "TokenData",
    "create_access_token",
    "decode_token",
    "jwt_auth",
    "jwt_secret_manager",
    "verify_token",
]
