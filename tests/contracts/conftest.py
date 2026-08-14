"""Shared fixtures for Direct and Gateway transport contract tests."""

from __future__ import annotations

import asyncio
import secrets
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE_SRC_DIRS = [
    _REPO_ROOT / "services" / "worker" / "src",
    _REPO_ROOT / "services" / "gateway" / "src",
    _REPO_ROOT / "packages" / "antcode_core" / "src",
    _REPO_ROOT / "packages" / "antcode_contracts" / "src",
]
for _src in _WORKSPACE_SRC_DIRS:
    source = str(_src)
    if _src.is_dir() and source not in sys.path:
        sys.path.insert(0, source)

from tests.contracts.fake_gateway import run_fake_gateway  # noqa: E402
from tests.contracts.transport_backends import (  # noqa: E402
    REDIS_TEST_HOST,
    REDIS_TEST_PORT,
    REDIS_TEST_URL,
    _tcp_reachable,
    cleanup_redis_transport,
    make_gateway_transport,
    make_redis_transport,
    produce_redis_task,
    redis_client,
)
from tests.contracts.transport_probe import ContractProbe  # noqa: E402


@dataclass(frozen=True)
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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:  # noqa: ARG001
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is not None and "transport_mode" in callspec.params:
            item.add_marker(pytest.mark.transport(callspec.params["transport_mode"]))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "transport(mode): transport implementation covered by a contract test",
    )


@pytest.fixture(params=["redis", "gateway"], ids=["redis", "gateway"])
def transport_mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest_asyncio.fixture
async def redis_admin(transport_mode: str) -> AsyncIterator[Any]:
    if transport_mode != "redis":
        yield None
        return

    async with redis_client() as client:
        try:
            await client.ping()
        except Exception as exc:
            pytest.fail(f"Redis not reachable at {REDIS_TEST_URL}: {exc}")
        yield client


@asynccontextmanager
async def _redis_transport_context(ids: FreshIds) -> AsyncIterator[Any]:
    transport = await make_redis_transport(ids)
    started = await transport.start()
    if not started:
        pytest.fail("redis transport failed to start")
    lease_id, expires_at_ms, _renew_after_ms, revoked = await transport.lease_renew(
        current_lease_id="",
        metrics=None,
    )
    if not lease_id or not expires_at_ms or revoked:
        pytest.fail("redis transport failed to acquire its initial lease")
    try:
        yield transport
    finally:
        await transport.stop(grace_period=1.0)
        await cleanup_redis_transport(transport)


@asynccontextmanager
async def _gateway_transport_context(ids: FreshIds) -> AsyncIterator[Any]:
    from antcode_gateway.handlers.poll import TaskPollHandler

    async with run_fake_gateway(
        ids.worker_id,
        visibility_timeout_ms=TaskPollHandler.PENDING_VISIBILITY_TIMEOUT_MS,
        task_payload_secret="contract-task-payload-secret-material-0001",
    ) as gateway:
        transport = await make_gateway_transport(ids, gateway)
        started = await transport.start()
        if not started:
            pytest.fail("gateway transport failed to start against fake server")
        # 与 redis fixture 对称：生产 lifecycle 在 engine 拉任务前必先取得
        # 初始 lease；StreamTasks/结算均按当前代际 fence（P1-GW-01）。
        lease_id, expires_at_ms, _renew_after_ms, revoked = await transport.lease_renew(
            current_lease_id="",
            metrics=None,
        )
        if not lease_id or not expires_at_ms or revoked:
            pytest.fail("gateway transport failed to acquire its initial lease")
        await _wait_until_gateway_ready(transport)
        try:
            yield transport
        finally:
            await transport.stop(grace_period=1.0)


async def _wait_until_gateway_ready(transport: Any) -> None:
    async def wait() -> None:
        while not transport.is_connected:
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(wait(), timeout=2.0)
    except TimeoutError:
        pytest.fail("gateway subscriptions did not become ready after initial lease")


@pytest_asyncio.fixture
async def transport(transport_mode: str, fresh_ids: FreshIds) -> AsyncIterator[Any]:
    context = (
        _redis_transport_context(fresh_ids) if transport_mode == "redis" else _gateway_transport_context(fresh_ids)
    )
    async with context as transport_obj:
        yield transport_obj


@pytest.fixture
def contract_probe(transport: Any, redis_admin: Any) -> ContractProbe:
    return ContractProbe(transport, redis_admin)


@pytest.fixture
def await_with_timeout() -> Callable[..., Any]:
    async def _wait(awaitable, timeout: float = 5.0):
        return await asyncio.wait_for(awaitable, timeout=timeout)

    return _wait


@pytest.fixture
def task_producer():
    async def _produce(transport_obj: Any, payload: dict[str, Any]) -> str:
        from antcode_worker.transport.base import TransportMode

        if transport_obj.mode == TransportMode.DIRECT:
            return await produce_redis_task(transport_obj, payload)
        state = getattr(transport_obj, "_test_gateway_state", None)
        if state is None:
            raise AssertionError("gateway transport missing fake state")
        return await state.enqueue_task(payload)

    return _produce
