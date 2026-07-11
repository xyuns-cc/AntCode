"""Redis 客户端统一 factory —— 支持 standalone / cluster / sentinel。

**为什么要抽这个层**：
项目里 9+ 处直接 `redis.asyncio.Redis.from_url(...)`，切集群时得逐个改。
这里做一个唯一入口，运维只需要改 `REDIS_URL` 的 scheme 或设 `REDIS_MODE`
env，代码无感知。

**约定**：
- `redis://` / `rediss://` → standalone（默认）
- `redis+cluster://host:port` / `rediss+cluster://` → Redis Cluster
- `redis+sentinel://<master_name>@host1:26379,host2:26379` → Sentinel

或者用 URL 用默认 scheme 但显式设 `REDIS_MODE=cluster|sentinel|standalone` env。
env 优先级高于 URL scheme。

**注意事项**：
- 集群模式下不同 slot 的 key 不能进同一个 pipeline(transaction=True) / Lua。
  仓库里目前只有 `lease_service` 的 Lua 违规，T1c 会用 hash tag 修掉。
- Sentinel 客户端拿到的是 master 只读；写从库操作走 `slave_for(...)`（本项目
  暂不需要）。
"""

from __future__ import annotations

import os
import platform
import socket
from typing import Any

import redis as sync_redis
import redis.asyncio as async_redis
from loguru import logger
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionExc
from redis.exceptions import TimeoutError as RedisTimeoutExc
from redis.retry import Retry as SyncRetry

from antcode_core.infrastructure.redis.sentinel_url import parse_sentinel_url as _parse_sentinel_url

REDIS_MODE_STANDALONE = "standalone"
REDIS_MODE_CLUSTER = "cluster"
REDIS_MODE_SENTINEL = "sentinel"

_VALID_MODES = {REDIS_MODE_STANDALONE, REDIS_MODE_CLUSTER, REDIS_MODE_SENTINEL}


def _detect_mode_from_url(url: str) -> str:
    """URL scheme → mode。未匹配则 standalone。"""
    lo = url.lower()
    if lo.startswith(("redis+cluster://", "rediss+cluster://")):
        return REDIS_MODE_CLUSTER
    if lo.startswith(("redis+sentinel://", "rediss+sentinel://")):
        return REDIS_MODE_SENTINEL
    return REDIS_MODE_STANDALONE


def _resolve_mode(url: str, mode_override: str | None) -> str:
    """env override 优先，其次看 URL scheme。"""
    env_mode = (mode_override or os.environ.get("REDIS_MODE", "")).strip().lower()
    if env_mode:
        if env_mode not in _VALID_MODES:
            raise ValueError(f"REDIS_MODE={env_mode!r} 无效，仅支持 {sorted(_VALID_MODES)}")
        return env_mode
    return _detect_mode_from_url(url)


def _normalize_url_for_client(url: str, mode: str) -> str:
    """去掉 `+cluster` / `+sentinel` 后缀，让底层 redis-py 认识。"""
    if mode == REDIS_MODE_CLUSTER:
        return url.replace("redis+cluster://", "redis://", 1).replace("rediss+cluster://", "rediss://", 1)
    if mode == REDIS_MODE_SENTINEL:
        return url.replace("redis+sentinel://", "redis://", 1).replace("rediss+sentinel://", "rediss://", 1)
    return url


def _build_common_kwargs(
    *,
    decode_responses: bool,
    max_connections: int | None,
    is_async: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    """跨模式复用的连接参数：retry / socket keepalive / health check。

    T7 fixup: sync 与 async 需要用不同的 Retry 类，之前误用 async 版 Retry
    到 sync 客户端会在第一次操作时崩（``'coroutine' object has no attribute
    'shutdown'``）。加 ``is_async`` 参数分派。
    """
    retry_cls = AsyncRetry if is_async else SyncRetry
    retry = retry_cls(ExponentialBackoff(cap=1.0, base=0.1), retries=3)
    kwargs: dict[str, Any] = {
        "retry_on_timeout": True,
        "retry": retry,
        "retry_on_error": [RedisConnectionExc, RedisTimeoutExc],
        "socket_timeout": 10,
        "socket_connect_timeout": 10,
        "socket_keepalive": True,
        "health_check_interval": 30,
        "encoding": "utf-8",
        "decode_responses": decode_responses,
    }
    if max_connections is not None:
        kwargs["max_connections"] = max_connections

    if platform.system() == "Linux":
        keepalive_options: dict[int, int] = {}
        if hasattr(socket, "TCP_KEEPIDLE"):
            keepalive_options[socket.TCP_KEEPIDLE] = 60
        if hasattr(socket, "TCP_KEEPINTVL"):
            keepalive_options[socket.TCP_KEEPINTVL] = 15
        if hasattr(socket, "TCP_KEEPCNT"):
            keepalive_options[socket.TCP_KEEPCNT] = 4
        if keepalive_options:
            kwargs["socket_keepalive_options"] = keepalive_options

    kwargs.update(overrides)
    return kwargs


def create_async_redis_client(
    url: str,
    *,
    decode_responses: bool = False,
    max_connections: int | None = None,
    mode: str | None = None,
    **overrides: Any,
) -> async_redis.Redis:
    """唯一异步 Redis 客户端入口。

    根据 URL scheme 或 `REDIS_MODE` env 返回 standalone / cluster / sentinel
    对应的 async 客户端。三者对外都实现基本 Redis 命令；集群/哨兵模式下调用
    方需自己保证 key 分布（同 hash tag / 单 key）。

    Args:
        url: Redis 连接串。scheme 决定模式，见模块 docstring。
        decode_responses: 是否解码为 str（默认 False，走 bytes wire）。
        max_connections: 单实例连接池上限；集群/哨兵下每个节点独立生效。
        mode: 显式指定 mode，覆盖 URL scheme + env（测试常用）。
        **overrides: 透传给底层 `from_url` / RedisCluster 构造器。

    Returns:
        `redis.asyncio.Redis` / `redis.asyncio.cluster.RedisCluster` /
        Sentinel master proxy —— 都可以 `await client.set(...)` 等。
    """
    if not url:
        raise ValueError("Redis URL 不能为空")

    resolved_mode = _resolve_mode(url, mode)

    if resolved_mode == REDIS_MODE_CLUSTER:
        from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster

        cluster_url = _normalize_url_for_client(url, resolved_mode)
        # RedisCluster 不接 max_connections（每节点独立池），拆出
        cluster_kwargs = _build_common_kwargs(
            decode_responses=decode_responses,
            max_connections=None,
            **overrides,
        )
        # RedisCluster 不吃这几个 kwargs
        for k in ("retry", "retry_on_timeout"):
            cluster_kwargs.pop(k, None)
        cluster_client = AsyncRedisCluster.from_url(cluster_url, **cluster_kwargs)
        logger.debug(f"Redis client 就绪 (mode=cluster, url={cluster_url})")
        return cluster_client  # type: ignore[return-value]

    if resolved_mode == REDIS_MODE_SENTINEL:
        from redis.asyncio.sentinel import Sentinel as AsyncSentinel

        master_name, endpoints, extra = _parse_sentinel_url(url)
        sentinel_kwargs = _build_common_kwargs(
            decode_responses=decode_responses,
            max_connections=max_connections,
            **overrides,
        )
        # Sentinel 构造只接受 socket_timeout / password / db
        sentinel_ctor_kwargs = {
            k: sentinel_kwargs[k] for k in ("socket_timeout", "socket_connect_timeout") if k in sentinel_kwargs
        }
        sentinel_ctor_kwargs.update(extra)
        sentinel = AsyncSentinel(endpoints, **sentinel_ctor_kwargs)
        master_kwargs = {key: extra[key] for key in ("password", "db") if key in extra}
        master_kwargs["decode_responses"] = decode_responses
        if max_connections is not None:
            master_kwargs["max_connections"] = max_connections
        sentinel_client = sentinel.master_for(
            master_name,
            redis_class=async_redis.Redis,
            **master_kwargs,
        )
        logger.debug(f"Redis client 就绪 (mode=sentinel, master={master_name}, endpoints={endpoints})")
        return sentinel_client

    # standalone
    standalone_url = _normalize_url_for_client(url, resolved_mode)
    pool_kwargs = _build_common_kwargs(
        decode_responses=decode_responses,
        max_connections=max_connections,
        **overrides,
    )
    pool = async_redis.ConnectionPool.from_url(standalone_url, **pool_kwargs)
    standalone_client = async_redis.Redis(connection_pool=pool)
    logger.debug(f"Redis client 就绪 (mode=standalone, url={standalone_url})")
    return standalone_client


def create_sync_redis_client(
    url: str,
    *,
    decode_responses: bool = False,
    max_connections: int | None = None,
    mode: str | None = None,
    **overrides: Any,
) -> sync_redis.Redis:
    """同步版本 —— 用于 Scrapy proxy 池等还没有 async 化的场景。

    集群模式返回 `redis.cluster.RedisCluster`，哨兵返回 sentinel master 代理。
    """
    if not url:
        raise ValueError("Redis URL 不能为空")

    resolved_mode = _resolve_mode(url, mode)

    if resolved_mode == REDIS_MODE_CLUSTER:
        from redis.cluster import RedisCluster as SyncRedisCluster

        cluster_url = _normalize_url_for_client(url, resolved_mode)
        cluster_kwargs = _build_common_kwargs(
            decode_responses=decode_responses,
            max_connections=None,
            is_async=False,
            **overrides,
        )
        for k in ("retry", "retry_on_timeout"):
            cluster_kwargs.pop(k, None)
        return SyncRedisCluster.from_url(cluster_url, **cluster_kwargs)  # type: ignore[return-value]

    if resolved_mode == REDIS_MODE_SENTINEL:
        from redis.sentinel import Sentinel as SyncSentinel

        master_name, endpoints, extra = _parse_sentinel_url(url)
        ctor_kwargs = {"socket_timeout": 10}
        ctor_kwargs.update(extra)
        sentinel = SyncSentinel(endpoints, **ctor_kwargs)
        master_kwargs = {key: extra[key] for key in ("password", "db") if key in extra}
        master_kwargs["decode_responses"] = decode_responses
        if max_connections is not None:
            master_kwargs["max_connections"] = max_connections
        return sentinel.master_for(
            master_name,
            redis_class=sync_redis.Redis,
            **master_kwargs,
        )

    standalone_url = _normalize_url_for_client(url, resolved_mode)
    pool_kwargs = _build_common_kwargs(
        decode_responses=decode_responses,
        max_connections=max_connections,
        is_async=False,
        **overrides,
    )
    pool = sync_redis.ConnectionPool.from_url(standalone_url, **pool_kwargs)
    return sync_redis.Redis(connection_pool=pool)


__all__ = [
    "REDIS_MODE_STANDALONE",
    "REDIS_MODE_CLUSTER",
    "REDIS_MODE_SENTINEL",
    "create_async_redis_client",
    "create_sync_redis_client",
]
