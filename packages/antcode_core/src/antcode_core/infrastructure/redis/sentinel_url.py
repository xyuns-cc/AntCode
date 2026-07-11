"""Strict Redis Sentinel URL parsing."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_SENTINEL_PORT = 26379


def _normalize(url: str) -> str:
    return url.replace("redis+sentinel://", "redis://", 1).replace(
        "rediss+sentinel://",
        "rediss://",
        1,
    )


def _split_location(url: str) -> tuple[str, str]:
    normalized = _normalize(url)
    location = normalized.partition("://")[2] if "://" in normalized else normalized
    authority, separator, db_text = location.partition("/")
    return authority, db_text if separator else ""


def _parse_identity(authority: str) -> tuple[str, str, str]:
    userinfo, separator, hosts = authority.rpartition("@")
    if not separator:
        return "", "", authority
    password, nested_separator, master = userinfo.rpartition("@")
    if not nested_separator:
        return "", userinfo, hosts
    return password, master, hosts


def _parse_endpoint(value: str) -> tuple[str, int]:
    host, separator, port_text = value.strip().rpartition(":")
    if not separator:
        host, port_text = value.strip(), str(DEFAULT_SENTINEL_PORT)
    if not host:
        raise ValueError("Redis Sentinel endpoint 缺少主机")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"Redis Sentinel 端口无效: {port_text!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"Redis Sentinel 端口超出范围: {port}")
    return host, port


def _parse_endpoints(hosts: str) -> list[tuple[str, int]]:
    endpoints = [_parse_endpoint(value) for value in hosts.split(",") if value.strip()]
    if not endpoints:
        raise ValueError("Redis Sentinel URL 至少需要一个 endpoint")
    return endpoints


def _parse_db(db_text: str) -> int | None:
    if not db_text:
        return None
    try:
        database = int(db_text)
    except ValueError as exc:
        raise ValueError(f"Redis database 无效: {db_text!r}") from exc
    if database < 0:
        raise ValueError("Redis database 不能为负数")
    return database


def parse_sentinel_url(url: str) -> tuple[str, list[tuple[str, int]], dict[str, Any]]:
    authority, db_text = _split_location(url)
    password, url_master, hosts = _parse_identity(authority)
    master = url_master or os.environ.get("REDIS_SENTINEL_MASTER_NAME", "").strip() or "mymaster"
    options: dict[str, Any] = {}
    if password:
        options["password"] = password
    database = _parse_db(db_text)
    if database is not None:
        options["db"] = database
    return master, _parse_endpoints(hosts), options


__all__ = ["parse_sentinel_url"]
