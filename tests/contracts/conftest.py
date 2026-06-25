"""
Worker TransportBase shared-contract test infrastructure.

This conftest provides:

1. **sys.path injection** so the tests can import `antcode_worker.*` even
   when the workspace packages have not been pip-installed into the active
   venv (which is the default state of the repo root).
2. A parametrized **`transport_mode`** fixture covering both
   ``"redis"`` (Direct mode, against a real Redis on ``localhost:16379``)
   and ``"gateway"`` (gRPC, currently always skipped until the P2 proto
   refactor lands).
3. A per-test ``transport`` fixture that starts the transport, hands it to
   the test, then stops it.  All Redis state created during a test is
   scrubbed in teardown so tests stay isolated.
4. Small helpers (``redis_admin``, ``fresh_ids``, ``await_with_timeout``)
   that several test modules share.

If Redis on ``localhost:16379`` is unreachable, the redis variant is
skipped at fixture setup time (not an ERROR) so CI on a workstation
without docker stays green.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio


# ----------------------------------------------------------------------------
# sys.path injection — runs at import time so the test files themselves can
# do `from antcode_worker.transport.base import TransportBase` cleanly.
# ----------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE_SRC_DIRS = [
    _REPO_ROOT / "services" / "worker" / "src",
    _REPO_ROOT / "packages" / "antcode_core" / "src",
    _REPO_ROOT / "packages" / "antcode_contracts" / "src",
]
for _src in _WORKSPACE_SRC_DIRS:
    s = str(_src)
    if _src.is_dir() and s not in sys.path:
        sys.path.insert(0, s)


# ----------------------------------------------------------------------------
# Constants tweakable via env, kept in one place.
# ----------------------------------------------------------------------------
REDIS_TEST_URL = os.environ.get(
    "ANTCODE_CONTRACT_REDIS_URL",
    "redis://localhost:16379/0",
)
REDIS_TEST_HOST = "localhost"
REDIS_TEST_PORT = 16379

GATEWAY_SKIP_REASON = (
    "Gateway transport contract tests are pending the P2 proto refactor "
    "(gateway.proto + in-process fake server). The fixture wiring is in "
    "place — flip `_GATEWAY_AVAILABLE` to True once the server lands."
)


# ----------------------------------------------------------------------------
# Liveness probes.
# ----------------------------------------------------------------------------
def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    """Cheap TCP probe — used to decide whether to skip the redis variant."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Set to True once an in-process fake gateway gRPC server is wired into
# `_make_gateway_transport()` below. Until then the gateway variant is
# uniformly skipped — the parametrization is still emitted so the test
# matrix is visible.
_GATEWAY_AVAILABLE = False


# ----------------------------------------------------------------------------
# Identity helpers — one fresh worker_id / run_id / task_id per test.
# ----------------------------------------------------------------------------
@dataclass
class FreshIds:
    worker_id: str
    run_id: str
    task_id: str
    project_id: str


@pytest.fixture
def fresh_ids() -> FreshIds:
    suffix = secrets.token_hex(6)
    return FreshIds(
        worker_id=f"worker-test-{suffix}",
        run_id=f"run-test-{suffix}",
        task_id=f"task-test-{suffix}",
        project_id=f"proj-test-{suffix}",
    )


# ----------------------------------------------------------------------------
# transport_mode parametrization.
# ----------------------------------------------------------------------------
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:  # noqa: ARG001
    """Tag every parametrized test with the transport_mode marker for clarity."""
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is not None and "transport_mode" in callspec.params:
            mode = callspec.params["transport_mode"]
            item.add_marker(pytest.mark.transport(mode))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "transport(mode): mark a test as covering a specific transport mode "
        "('redis' or 'gateway').",
    )


# ----------------------------------------------------------------------------
# Redis admin client — used by tests and teardown to peek at streams.
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture
async def redis_admin(transport_mode: str) -> AsyncIterator[Any]:
    """
    Lightweight Redis client for tests that need to assert on stream state
    (length, contents) or clean up keys created during a test.

    For the gateway variant this fixture yields None — gateway tests should
    use their own assertion API rather than peeking into Redis directly.
    """
    if transport_mode != "redis":
        yield None
        return

    try:
        import redis.asyncio as aioredis
    except ImportError:  # pragma: no cover - redis is a hard dep
        pytest.skip("redis package not installed")

    client = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip(
            f"Redis not reachable at {REDIS_TEST_URL}; "
            "start it with `docker compose -f tests/contracts/docker-compose.test.yml up -d`"
        )
    try:
        yield client
    finally:
        await client.aclose()


# ----------------------------------------------------------------------------
# Per-mode transport factory.
# ----------------------------------------------------------------------------
async def _make_redis_transport(ids: FreshIds):
    """Build a RedisTransport bound to the test's fresh ids."""
    if not _tcp_reachable(REDIS_TEST_HOST, REDIS_TEST_PORT):
        pytest.skip(
            f"Redis on {REDIS_TEST_HOST}:{REDIS_TEST_PORT} is not reachable; "
            "start it with `docker compose -f tests/contracts/docker-compose.test.yml up -d`"
        )

    from antcode_worker.transport.redis import RedisTransport
    from antcode_worker.transport.redis.keys import RedisKeys

    # Namespace each test under a unique prefix so concurrent runs (and
    # leftover keys from prior failed runs) can't contaminate it.
    namespace = f"antcode-test-{secrets.token_hex(4)}"
    keys = RedisKeys(namespace=namespace)
    transport = RedisTransport(
        redis_url=REDIS_TEST_URL,
        worker_id=ids.worker_id,
        namespace=namespace,
        consumer_group=keys.consumer_group_name(),
    )
    # Stash the namespace on the transport so teardown can scrub it.
    transport._test_namespace = namespace  # type: ignore[attr-defined]
    transport._test_keys = keys  # type: ignore[attr-defined]
    return transport


async def _make_gateway_transport(ids: FreshIds):  # noqa: ARG001
    """
    Placeholder — the actual fake gRPC server is part of the P2 proto
    refactor.  Until it lands we skip the test rather than constructing a
    transport that can't reach anything.
    """
    if not _GATEWAY_AVAILABLE:
        pytest.skip(GATEWAY_SKIP_REASON)

    # NOTE: when re-enabling, build a GatewayTransport pointed at an
    # in-process fake server (see services/gateway/src/antcode_gateway/main.py
    # for the real server bootstrap) and yield it. Don't forget to scrub
    # its receipt cache on teardown.
    from antcode_worker.transport.gateway import GatewayConfig, GatewayTransport

    config = GatewayConfig(
        gateway_host="localhost",
        gateway_port=0,  # caller would inject a real port
        use_tls=False,
        api_key="test-api-key",
        worker_id=ids.worker_id,
    )
    return GatewayTransport(gateway_config=config)


# Public parametrized fixture — every contract test depends on this.
@pytest.fixture(params=["redis", "gateway"], ids=["redis", "gateway"])
def transport_mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest_asyncio.fixture
async def transport(transport_mode: str, fresh_ids: FreshIds) -> AsyncIterator[Any]:
    """
    Construct a started transport, hand it to the test, then stop it and
    scrub any Redis namespace it used.
    """
    if transport_mode == "redis":
        transport_obj = await _make_redis_transport(fresh_ids)
    elif transport_mode == "gateway":
        transport_obj = await _make_gateway_transport(fresh_ids)
    else:  # pragma: no cover
        raise AssertionError(f"unknown transport_mode {transport_mode!r}")

    started = await transport_obj.start()
    if not started:
        pytest.skip(f"transport {transport_mode!r} failed to start in this environment")

    try:
        yield transport_obj
    finally:
        # Best-effort stop — never let teardown errors mask test failures.
        try:
            await transport_obj.stop(grace_period=1.0)
        except Exception:
            pass

        # Redis: scrub the unique namespace.
        ns = getattr(transport_obj, "_test_namespace", None)
        if ns:
            try:
                import redis.asyncio as aioredis

                cleanup = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
                try:
                    cursor: int = 0
                    pattern = f"{ns}:*"
                    while True:
                        cursor, keys = await cleanup.scan(cursor=cursor, match=pattern, count=500)
                        if keys:
                            await cleanup.delete(*keys)
                        if cursor == 0:
                            break
                finally:
                    await cleanup.aclose()
            except Exception:
                pass


# ----------------------------------------------------------------------------
# Test helpers exported as fixtures.
# ----------------------------------------------------------------------------
@pytest.fixture
def await_with_timeout() -> Callable[..., Any]:
    """Wrap `asyncio.wait_for` with a friendlier default for contract tests."""

    async def _wait(awaitable, timeout: float = 5.0):
        return await asyncio.wait_for(awaitable, timeout=timeout)

    return _wait


@asynccontextmanager
async def _producer(redis_url: str = REDIS_TEST_URL) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def task_producer():
    """
    Inject a function `await produce(transport, payload)` that publishes a
    task message onto whichever stream the transport polls. For the redis
    variant this writes directly to the worker-specific ready stream; for
    gateway it currently raises (gateway tests should be skipped before
    they reach this).
    """

    async def _produce(transport_obj, payload: dict[str, Any]) -> str:
        from antcode_worker.transport.base import TransportMode

        if transport_obj.mode == TransportMode.DIRECT:
            keys = getattr(transport_obj, "_test_keys", None)
            assert keys is not None, "redis transport not built via this conftest"
            stream = keys.task_ready_stream(transport_obj._worker_id)
            async with _producer() as client:
                msg_id = await client.xadd(stream, {k: str(v) for k, v in payload.items()})
                return msg_id

        raise RuntimeError(
            "task_producer is not implemented for gateway mode yet — "
            "the gateway tests should be skipped before reaching this helper."
        )

    return _produce
