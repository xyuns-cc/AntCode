"""Secret redaction contract for generic system configuration APIs."""

SECRET_MASK = "***REDACTED***"
_SENSITIVE_KEY_PARTS = (
    "access_key",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
    "webhook",
)
_SENSITIVE_CONFIG_KEYS = frozenset({"email_config"})


def is_sensitive_config_key(config_key: str) -> bool:
    normalized = config_key.strip().lower()
    return normalized in _SENSITIVE_CONFIG_KEYS or any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_config_value(config_key: str, value: str) -> str:
    if value and is_sensitive_config_key(config_key):
        return SECRET_MASK
    return value


__all__ = ["SECRET_MASK", "is_sensitive_config_key", "redact_config_value"]
