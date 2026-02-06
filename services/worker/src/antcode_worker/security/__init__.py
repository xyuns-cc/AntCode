"""
安全模块

提供 Worker 身份管理、凭证管理和认证验证功能。

Requirements: 11.1, 11.2, 11.3
"""

from antcode_worker.security.identity import (
    Identity,
    IdentityManager,
    get_identity_manager,
    init_identity_manager,
    set_identity_manager,
)
from antcode_worker.security.secrets import (
    Credential,
    SecretsManager,
    get_secrets_manager,
    init_secrets_manager,
    set_secrets_manager,
)
from antcode_worker.security.verify import (
    AuthMethod,
    DispatchVerifier,
    TaskSignature,
    VerificationContext,
    Verifier,
    VerifyResult,
    get_dispatch_verifier,
    get_task_verifier,
    init_verifiers,
    set_dispatch_verifier,
    set_task_verifier,
)

__all__ = [
    # identity
    "Identity",
    "IdentityManager",
    "get_identity_manager",
    "set_identity_manager",
    "init_identity_manager",
    # secrets
    "Credential",
    "SecretsManager",
    "get_secrets_manager",
    "set_secrets_manager",
    "init_secrets_manager",
    # verify
    "AuthMethod",
    "VerifyResult",
    "TaskSignature",
    "VerificationContext",
    "Verifier",
    "DispatchVerifier",
    "get_task_verifier",
    "set_task_verifier",
    "get_dispatch_verifier",
    "set_dispatch_verifier",
    "init_verifiers",
]
