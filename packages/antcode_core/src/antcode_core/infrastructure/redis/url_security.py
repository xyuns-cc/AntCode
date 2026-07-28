"""Redis URL display helpers."""

from urllib.parse import unquote

_SENTINEL_SCHEMES = {"redis+sentinel", "rediss+sentinel"}


def redact_redis_url(url: str) -> str:
    """Return a Redis URL with any embedded password replaced."""
    scheme, separator, location = url.partition("://")
    if not separator:
        return url

    location, fragment_separator, fragment = location.partition("#")
    base, query_separator, query = location.partition("?")
    authority, path_separator, path = base.partition("/")
    redacted_authority = _redact_authority(scheme.lower(), authority)
    suffix = f"/{path}" if path_separator else ""
    query_suffix = f"?{_redact_query(query)}" if query_separator else ""
    fragment_suffix = f"#{fragment}" if fragment_separator else ""
    return f"{scheme}://{redacted_authority}{suffix}{query_suffix}{fragment_suffix}"


def _redact_authority(scheme: str, authority: str) -> str:
    if scheme in _SENTINEL_SCHEMES:
        return _redact_sentinel_authority(authority)

    userinfo, separator, hosts = authority.rpartition("@")
    if not separator or ":" not in userinfo:
        return authority
    username, _password = userinfo.split(":", 1)
    return f"{username}:***@{hosts}"


def _redact_sentinel_authority(authority: str) -> str:
    credentials, separator, remainder = authority.partition("@")
    if not separator or "@" not in remainder:
        return authority
    username, password_separator, _password = credentials.partition(":")
    redacted = f"{username}:***" if password_separator else "***"
    return f"{redacted}@{remainder}"


def _redact_query(query: str) -> str:
    redacted: list[str] = []
    for item in query.split("&"):
        key, separator, _value = item.partition("=")
        if separator and unquote(key).lower().endswith("password"):
            redacted.append(f"{key}=***")
        else:
            redacted.append(item)
    return "&".join(redacted)


__all__ = ["redact_redis_url"]
