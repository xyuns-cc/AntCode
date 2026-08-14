from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.workers import worker_capability_routing as routing

LEASE_GENERATION = b"7"


def test_project_types_are_normalized_to_executable_plugins() -> None:
    required = routing.required_execution_task_types(
        [
            {"project_type": "file"},
            {"project_type": "rule"},
            {},
        ]
    )

    assert required == frozenset({"code", "rule"})
    assert routing.supports_task_types({"task_types": ["code", "rule"]}, required)
    assert not routing.supports_task_types({"task_types": ["code"]}, required)


def test_template_render_plugin_is_not_a_browser_capability() -> None:
    assert not routing.has_render_capability({"task_types": ["render"]})
    assert routing.has_render_capability({"playwright": {"enabled": True}})


@pytest.mark.asyncio
async def test_gateway_capabilities_are_read_from_authoritative_lease(monkeypatch) -> None:
    redis = SimpleNamespace(
        eval=AsyncMock(return_value=[b"lease-1", b'{"task_types":["code","rule"]}', LEASE_GENERATION])
    )
    monkeypatch.setattr(routing, "get_redis_client", AsyncMock(return_value=redis))
    worker = SimpleNamespace(
        id=7,
        public_id="worker-7",
        transport_mode="gateway",
        capabilities={"task_types": ["stale"]},
    )

    result = await routing.resolve_capability_map([worker], authoritative=True)

    assert result == {7: {"task_types": ["code", "rule"]}}
    assert worker.capabilities == {"task_types": ["stale"]}


@pytest.mark.asyncio
async def test_gateway_without_lease_capabilities_fails_closed(monkeypatch) -> None:
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"lease-1", b"", LEASE_GENERATION]))
    monkeypatch.setattr(routing, "get_redis_client", AsyncMock(return_value=redis))
    worker = SimpleNamespace(id=7, public_id="worker-7", transport_mode="gateway", capabilities={})

    assert await routing.resolve_capability_map([worker], authoritative=True) == {7: {}}


@pytest.mark.asyncio
async def test_expired_gateway_lease_capabilities_fail_closed(monkeypatch) -> None:
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"", b"", b""]))
    monkeypatch.setattr(routing, "get_redis_client", AsyncMock(return_value=redis))
    worker = SimpleNamespace(id=7, public_id="worker-7", transport_mode="gateway", capabilities={})

    assert await routing.resolve_capability_map([worker], authoritative=True) == {7: {}}


@pytest.mark.asyncio
async def test_invalid_candidate_does_not_hide_healthy_candidate(monkeypatch) -> None:
    invalid = SimpleNamespace(id=1, public_id="bad", transport_mode="gateway", capabilities={})
    healthy = SimpleNamespace(id=2, public_id="good", transport_mode="direct", capabilities={"task_types": ["code"]})
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"lease-1", b"not-json", LEASE_GENERATION]))
    monkeypatch.setattr(routing, "get_redis_client", AsyncMock(return_value=redis))

    assert await routing.resolve_capability_map([invalid, healthy], authoritative=True) == {
        1: {},
        2: {"task_types": ["code"]},
    }


@pytest.mark.asyncio
async def test_gateway_candidates_use_one_atomic_snapshot_read(monkeypatch) -> None:
    workers = [
        SimpleNamespace(id=1, public_id="one", transport_mode="gateway", capabilities={}),
        SimpleNamespace(id=2, public_id="two", transport_mode="gateway", capabilities={}),
    ]
    redis = SimpleNamespace(
        eval=AsyncMock(
            return_value=[
                b"lease-1",
                b'{"task_types":["code"]}',
                b"7",
                b"lease-2",
                b'{"task_types":["rule"]}',
                b"8",
            ]
        )
    )
    monkeypatch.setattr(routing, "get_redis_client", AsyncMock(return_value=redis))

    result = await routing.resolve_capability_map(workers, authoritative=True)

    assert result == {1: {"task_types": ["code"]}, 2: {"task_types": ["rule"]}}
    redis.eval.assert_awaited_once()
