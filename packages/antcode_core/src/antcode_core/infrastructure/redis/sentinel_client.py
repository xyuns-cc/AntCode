"""Shared sync/async Redis Sentinel client assembly."""

from __future__ import annotations

from typing import Any

import redis as sync_redis
import redis.asyncio as async_redis
from redis.asyncio.sentinel import Sentinel as AsyncSentinel
from redis.sentinel import Sentinel as SyncSentinel

from antcode_core.infrastructure.redis.sentinel_url import SentinelUrl, parse_sentinel_url

_TLS_OPTION_PREFIX = "ssl_"


def _validate_transport(config: SentinelUrl, kwargs: dict[str, Any]) -> None:
    explicit_ssl = kwargs.get("ssl")
    if explicit_ssl is not None:
        if not isinstance(explicit_ssl, bool) or explicit_ssl != config.tls:
            raise ValueError("Redis Sentinel TLS 配置与 URL scheme 冲突")
    has_tls_options = any(key.startswith(_TLS_OPTION_PREFIX) for key in kwargs)
    if has_tls_options and not config.tls:
        raise ValueError("Redis Sentinel TLS 参数要求 rediss+sentinel scheme")
    if ("ssl_certfile" in kwargs) != ("ssl_keyfile" in kwargs):
        raise ValueError("Redis Sentinel client cert 和 key 必须同时配置")


def _apply_tls_options(config: SentinelUrl, kwargs: dict[str, Any]) -> None:
    for key, value in config.tls_options:
        _set_url_option(kwargs, key, value)


def _set_url_option(
    kwargs: dict[str, Any],
    key: str,
    value: str | int | None,
) -> None:
    if value is None:
        return
    existing = kwargs.get(key)
    if existing is not None and existing != value:
        raise ValueError(f"Redis Sentinel {key} 配置来源冲突")
    kwargs[key] = value


def _master_kwargs(config: SentinelUrl, common_kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs = dict(common_kwargs)
    _validate_transport(config, kwargs)
    _set_url_option(kwargs, "username", config.master_username)
    _set_url_option(kwargs, "password", config.master_password)
    _set_url_option(kwargs, "db", config.database)
    _apply_tls_options(config, kwargs)
    if config.tls:
        kwargs["ssl"] = True
    return kwargs


def _sentinel_kwargs(config: SentinelUrl, common_kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs = dict(common_kwargs)
    kwargs.pop("db", None)
    kwargs.pop("username", None)
    kwargs.pop("password", None)
    _validate_transport(config, kwargs)
    _set_url_option(kwargs, "username", config.sentinel_username)
    _set_url_option(kwargs, "password", config.sentinel_password)
    _apply_tls_options(config, kwargs)
    if config.tls:
        kwargs["ssl"] = True
    return kwargs


def create_async_sentinel_client(
    url: str,
    *,
    common_kwargs: dict[str, Any],
) -> async_redis.Redis:
    config = parse_sentinel_url(url)
    sentinel = AsyncSentinel(
        list(config.endpoints),
        sentinel_kwargs=_sentinel_kwargs(config, common_kwargs),
        **_master_kwargs(config, common_kwargs),
    )
    return sentinel.master_for(config.master_name, redis_class=async_redis.Redis)


def create_sync_sentinel_client(
    url: str,
    *,
    common_kwargs: dict[str, Any],
) -> sync_redis.Redis:
    config = parse_sentinel_url(url)
    sentinel = SyncSentinel(
        list(config.endpoints),
        sentinel_kwargs=_sentinel_kwargs(config, common_kwargs),
        **_master_kwargs(config, common_kwargs),
    )
    return sentinel.master_for(config.master_name, redis_class=sync_redis.Redis)


__all__ = ["create_async_sentinel_client", "create_sync_sentinel_client"]
