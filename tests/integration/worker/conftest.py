"""Worker integration test helpers."""

import pytest
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError


_ORIGINAL_FROM_URL = aioredis.from_url


def _patched_from_url(url, *args, **kwargs):
    kwargs.setdefault("retry_on_timeout", True)
    kwargs.setdefault(
        "retry",
        Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=5),
    )
    kwargs.setdefault(
        "retry_on_error",
        [ConnectionError, TimeoutError],
    )
    kwargs.setdefault("socket_timeout", 30)
    kwargs.setdefault("socket_connect_timeout", 10)
    kwargs.setdefault("socket_keepalive", True)
    kwargs.setdefault("health_check_interval", 30)
    kwargs.setdefault("max_connections", 200)
    return _ORIGINAL_FROM_URL(url, *args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def _patch_redis_from_url():
    aioredis.from_url = _patched_from_url
    yield
    aioredis.from_url = _ORIGINAL_FROM_URL
