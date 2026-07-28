"""Strict Redis Sentinel URL parsing without credential disclosure."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlsplit

DEFAULT_SENTINEL_PORT = 26379
MAX_PORT = 65535
CONTROL_CHARACTER_LIMIT = 32
_SENTINEL_SCHEMES = {"redis", "rediss", "redis+sentinel", "rediss+sentinel"}
_QUERY_KEYS = {
    "sentinel_username",
    "sentinel_password",
    "master_username",
    "master_password",
    "ssl_ca_certs",
    "ssl_certfile",
    "ssl_keyfile",
}
MAX_HOSTNAME_LENGTH = 253
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True, slots=True)
class SentinelUrl:
    master_name: str
    endpoints: tuple[tuple[str, int], ...]
    database: int | None
    tls: bool
    sentinel_username: str | None = None
    sentinel_password: str | None = field(repr=False, default=None)
    master_username: str | None = None
    master_password: str | None = field(repr=False, default=None)
    tls_options: tuple[tuple[str, str], ...] = ()


def _split_identity(authority: str) -> tuple[str | None, str, str]:
    identity, separator, hosts = authority.rpartition("@")
    if not separator:
        configured_master = os.environ.get("REDIS_SENTINEL_MASTER_NAME", "mymaster")
        return None, _required(configured_master, "master name"), authority
    legacy_auth, nested_separator, master_name = identity.rpartition("@")
    if not nested_separator:
        return None, _required(unquote(identity), "master name"), hosts
    return legacy_auth, _required(unquote(master_name), "master name"), hosts


def _required(value: str, label: str) -> str:
    validated = value.strip()
    if not validated or any(character.isspace() or ord(character) < CONTROL_CHARACTER_LIMIT for character in validated):
        raise ValueError(f"Redis Sentinel {label} 无效")
    return validated


def _parse_legacy_auth(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    username, separator, password = value.partition(":")
    if not separator:
        return None, _required(unquote(username), "master password")
    return (
        _required(unquote(username), "master username"),
        _required(unquote(password), "master password"),
    )


def _parse_port(port_text: str) -> int:
    if not port_text:
        return DEFAULT_SENTINEL_PORT
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("Redis Sentinel 端口无效") from exc
    if not 1 <= port <= MAX_PORT:
        raise ValueError("Redis Sentinel 端口超出范围")
    return port


def _validate_host(host: str) -> str:
    candidate = unquote(host).strip()
    if not candidate:
        raise ValueError("Redis Sentinel endpoint 缺少主机")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        labels = candidate.split(".")
        if len(candidate) > MAX_HOSTNAME_LENGTH or not all(_HOST_LABEL_RE.fullmatch(label) for label in labels):
            raise ValueError("Redis Sentinel endpoint 主机无效") from None
    return candidate.lower()


def _parse_endpoint(value: str) -> tuple[str, int]:
    endpoint = value.strip()
    if endpoint.startswith("["):
        closing = endpoint.find("]")
        if closing < 0 or endpoint[closing + 1 : closing + 2] not in {"", ":"}:
            raise ValueError("Redis Sentinel IPv6 endpoint 无效")
        return _validate_host(endpoint[1:closing]), _parse_port(endpoint[closing + 2 :])
    if endpoint.count(":") > 1:
        raise ValueError("Redis Sentinel IPv6 endpoint 必须使用方括号")
    host, separator, port_text = endpoint.rpartition(":")
    return _validate_host(host if separator else endpoint), _parse_port(port_text if separator else "")


def _parse_endpoints(hosts: str) -> tuple[tuple[str, int], ...]:
    raw_endpoints = hosts.split(",")
    if any(not value.strip() for value in raw_endpoints):
        raise ValueError("Redis Sentinel URL 包含空 endpoint")
    endpoints = tuple(_parse_endpoint(value) for value in raw_endpoints)
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("Redis Sentinel URL 包含重复 endpoint")
    return endpoints


def _parse_database(path: str) -> int | None:
    if path in {"", "/"}:
        return None
    db_text = path.removeprefix("/")
    if "/" in db_text or not db_text.isdecimal():
        raise ValueError("Redis database 无效")
    return int(db_text)


def _parse_query(query: str) -> dict[str, str]:
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("Redis Sentinel 查询参数格式无效") from exc
    options: dict[str, str] = {}
    for key, value in pairs:
        if key not in _QUERY_KEYS:
            raise ValueError(f"Redis Sentinel 查询参数不支持: {key!r}")
        if key in options:
            raise ValueError(f"Redis Sentinel 查询参数重复: {key!r}")
        options[key] = _required(value, key)
    _validate_credential_pair(options, "sentinel")
    _validate_credential_pair(options, "master")
    if ("ssl_certfile" in options) != ("ssl_keyfile" in options):
        raise ValueError("Redis Sentinel client cert 和 key 必须同时配置")
    return options


def _validate_credential_pair(options: dict[str, str], prefix: str) -> None:
    if f"{prefix}_username" in options and f"{prefix}_password" not in options:
        raise ValueError(f"Redis {prefix} username 必须同时配置 password")


def parse_sentinel_url(url: str) -> SentinelUrl:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in _SENTINEL_SCHEMES:
        raise ValueError("Redis Sentinel URL scheme 无效")
    if parsed.fragment:
        raise ValueError("Redis Sentinel URL 不允许 fragment")
    legacy_auth, master_name, hosts = _split_identity(parsed.netloc)
    legacy_username, legacy_password = _parse_legacy_auth(legacy_auth)
    options = _parse_query(parsed.query)
    if legacy_auth is not None and any(key.startswith("master_") for key in options):
        raise ValueError("Redis master 凭据不能同时使用 authority 和查询参数")
    tls_options = tuple((key, options[key]) for key in sorted(options) if key.startswith("ssl_"))
    tls = parsed.scheme.lower().startswith("rediss")
    if tls_options and not tls:
        raise ValueError("Redis Sentinel TLS 参数要求 rediss+sentinel scheme")
    return SentinelUrl(
        master_name=master_name,
        endpoints=_parse_endpoints(hosts),
        database=_parse_database(parsed.path),
        tls=tls,
        sentinel_username=options.get("sentinel_username"),
        sentinel_password=options.get("sentinel_password"),
        master_username=options.get("master_username", legacy_username),
        master_password=options.get("master_password", legacy_password),
        tls_options=tls_options,
    )


__all__ = ["SentinelUrl", "parse_sentinel_url"]
