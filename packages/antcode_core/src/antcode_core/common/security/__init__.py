"""
Security 模块

安全相关功能：
- auth: JWT 令牌处理与 FastAPI 鉴权依赖
- api_key: API Key 认证
- mtls: mTLS 认证
- permissions: 权限管理
- hmac_utils: HMAC 签名工具
"""

from antcode_core.common.security.api_key import (
    APIKeyManager,
    api_key_manager,
    finalize_worker_api_key_rotation,
    generate_api_key,
    hash_api_key,
    rotate_worker_api_key,
    verify_api_key,
    verify_api_key_hash,
)
from antcode_core.common.security.auth import (
    JWTAuth,
    JWTSecretManager,
    TokenData,
    jwt_auth,
    jwt_secret_manager,
)
from antcode_core.common.security.hmac_utils import (
    WORKER_HTTP_SIGNATURE_HEADER,
    WORKER_HTTP_SIGNATURE_VERSION,
    canonicalize_http_method,
    canonicalize_http_path,
    compute_hmac,
    constant_time_compare,
    generate_hmac_signature,
    request_body_sha256,
    verify_hmac_signature,
)
from antcode_core.common.security.login_crypto import (
    LOGIN_ENCRYPTION_ALGORITHM,
    LoginPasswordCrypto,
    LoginPasswordCryptoError,
    login_password_crypto,
)
from antcode_core.common.security.permissions import (
    Permission,
    check_all_permissions,
    check_any_permission,
    check_permission,
    get_role_permissions,
)

__all__ = [
    # auth
    "JWTAuth",
    "JWTSecretManager",
    "TokenData",
    "jwt_auth",
    "jwt_secret_manager",
    # api_key
    "APIKeyManager",
    "api_key_manager",
    "finalize_worker_api_key_rotation",
    "generate_api_key",
    "hash_api_key",
    "rotate_worker_api_key",
    "verify_api_key",
    "verify_api_key_hash",
    # permissions
    "Permission",
    "check_all_permissions",
    "check_any_permission",
    "check_permission",
    "get_role_permissions",
    # hmac_utils
    "WORKER_HTTP_SIGNATURE_HEADER",
    "WORKER_HTTP_SIGNATURE_VERSION",
    "canonicalize_http_method",
    "canonicalize_http_path",
    "compute_hmac",
    "constant_time_compare",
    "generate_hmac_signature",
    "request_body_sha256",
    "verify_hmac_signature",
    # login crypto
    "LOGIN_ENCRYPTION_ALGORITHM",
    "LoginPasswordCrypto",
    "LoginPasswordCryptoError",
    "login_password_crypto",
]
