"""
Security 模块

安全相关功能：
- jwt: JWT 令牌处理
- api_key: API Key 认证
- mtls: mTLS 认证
- permissions: 权限管理
- hmac_utils: HMAC 签名工具
"""

from antcode_core.common.security.api_key import (
    APIKeyManager,
    api_key_manager,
    generate_api_key,
    hash_api_key,
    verify_api_key,
    verify_api_key_hash,
)
from antcode_core.common.security.hmac_utils import (
    compute_hmac,
    constant_time_compare,
    generate_hmac_signature,
    verify_hmac_signature,
)
from antcode_core.common.security.jwt import (
    JWTAuth,
    JWTSecretManager,
    TokenData,
    create_access_token,
    decode_token,
    jwt_auth,
    jwt_secret_manager,
    verify_token,
)
from antcode_core.common.security.permissions import (
    Permission,
    check_all_permissions,
    check_any_permission,
    check_permission,
    get_role_permissions,
)
from antcode_core.common.security.login_crypto import (
    LOGIN_ENCRYPTION_ALGORITHM,
    LoginPasswordCrypto,
    LoginPasswordCryptoError,
    login_password_crypto,
)

__all__ = [
    # jwt
    "JWTAuth",
    "JWTSecretManager",
    "TokenData",
    "create_access_token",
    "decode_token",
    "jwt_auth",
    "jwt_secret_manager",
    "verify_token",
    # api_key
    "APIKeyManager",
    "api_key_manager",
    "generate_api_key",
    "hash_api_key",
    "verify_api_key",
    "verify_api_key_hash",
    # permissions
    "Permission",
    "check_all_permissions",
    "check_any_permission",
    "check_permission",
    "get_role_permissions",
    # hmac_utils
    "compute_hmac",
    "constant_time_compare",
    "generate_hmac_signature",
    "verify_hmac_signature",
    # login crypto
    "LOGIN_ENCRYPTION_ALGORITHM",
    "LoginPasswordCrypto",
    "LoginPasswordCryptoError",
    "login_password_crypto",
]
