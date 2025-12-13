"""
安全模块

包含用户认证和节点认证相关功能
"""
from src.core.security.auth import (
    AUTH_ERROR,
    TOKEN_EXPIRED_ERROR,
    JWTSecretManager,
    jwt_secret_manager,
    TokenData,
    JWTAuth,
    jwt_auth,
    security,
    get_current_user,
    get_current_user_id,
    get_current_user_from_token,
    verify_token,
    get_current_admin_user,
    get_current_super_admin,
    verify_super_admin,
    get_optional_current_user,
)
from src.core.security.node_auth import (
    NodeAuthVerifier,
    node_auth_verifier,
    verify_node_request,
    verify_node_request_with_signature,
)

__all__ = [
    # auth.py exports
    "AUTH_ERROR",
    "TOKEN_EXPIRED_ERROR",
    "JWTSecretManager",
    "jwt_secret_manager",
    "TokenData",
    "JWTAuth",
    "jwt_auth",
    "security",
    "get_current_user",
    "get_current_user_id",
    "get_current_user_from_token",
    "verify_token",
    "get_current_admin_user",
    "get_current_super_admin",
    "verify_super_admin",
    "get_optional_current_user",
    # node_auth.py exports
    "NodeAuthVerifier",
    "node_auth_verifier",
    "verify_node_request",
    "verify_node_request_with_signature",
]
