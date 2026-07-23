"""Redis URL display helpers."""

_SENTINEL_SCHEMES = {"redis+sentinel", "rediss+sentinel"}


def redact_redis_url(url: str) -> str:
    """Return a Redis URL with any embedded password replaced."""
    scheme, separator, location = url.partition("://")
    if not separator:
        return url

    authority, path_separator, path = location.partition("/")
    redacted_authority = _redact_authority(scheme.lower(), authority)
    suffix = f"/{path}" if path_separator else ""
    return f"{scheme}://{redacted_authority}{suffix}"


def _redact_authority(scheme: str, authority: str) -> str:
    if scheme in _SENTINEL_SCHEMES:
        return _redact_sentinel_authority(authority)

    userinfo, separator, hosts = authority.rpartition("@")
    if not separator or ":" not in userinfo:
        return authority
    username, _password = userinfo.split(":", 1)
    return f"{username}:***@{hosts}"


def _redact_sentinel_authority(authority: str) -> str:
    password, separator, remainder = authority.partition("@")
    if not separator or "@" not in remainder:
        return authority
    return f"***@{remainder}"


__all__ = ["redact_redis_url"]
