"""Worker configuration display sanitization."""

from typing import Any

from antcode_core.common.log_sanitization import sanitize_dict
from antcode_core.infrastructure.redis.url_security import redact_redis_url

SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "access_key",
        "api_key",
        "authorization",
        "client_key",
        "encryption_key",
        "gateway_token",
        "install_key",
        "jwt_secret",
        "password",
        "private_key",
        "secret",
        "token",
        "worker_key",
    }
)


def sanitize_config_for_display(config: dict[str, Any]) -> dict[str, Any]:
    """Mask credentials while retaining non-sensitive diagnostic settings."""
    sanitized = sanitize_dict(config, sensitive_keys=SENSITIVE_CONFIG_KEYS)
    redis_url = config.get("redis_url")
    if isinstance(redis_url, str):
        sanitized["redis_url"] = redact_redis_url(redis_url)
    return sanitized
