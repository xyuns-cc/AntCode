"""混跑门禁：旧 Worker 契约 + 新 Gateway 必须响亮失败，而不是静默零产出。

未加门禁前的真实行为（滚动升级实测）：旧 Worker 照样
拿到 Lease，Gateway 随后把心跳写进 ``{ns}:heartbeat:{id}``，控制台显示在线健康，而
它执行的任务 ``params`` / ``environment`` 全空。本文件把这条路彻底堵死。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import common_pb2, control_pb2
from antcode_contracts.transcode import encode_capabilities
from antcode_contracts.wire_contract import (
    WIRE_CONTRACT_CAPABILITY,
    WORKER_WIRE_CONTRACT_VERSION,
    wire_contract_capability,
)
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService

LEASE_TTL_MS = 30_000
LEASE_RENEW_AFTER_MS = 10_000
FUTURE_CONTRACT_VERSION = WORKER_WIRE_CONTRACT_VERSION + 1

# origin/main(HEAD) 的 Worker 上报的能力快照：没有 wire_contract。
LEGACY_WORKER_CAPABILITIES: dict[str, object] = {"curl_cffi": {"enabled": True}, "task_types": ["code"]}


def _store() -> MagicMock:
    lease = SimpleNamespace(worker_id="worker-1", lease_id="lease-1", expires_at_ms=LEASE_TTL_MS)
    return MagicMock(
        policy=SimpleNamespace(ttl_ms=LEASE_TTL_MS, renew_after_ms=LEASE_RENEW_AFTER_MS),
        grant=AsyncMock(return_value=lease),
        revoke=AsyncMock(return_value=True),
    )


def _service(monkeypatch, store: MagicMock, handler: MagicMock) -> GatewayControlService:
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-1"),
    )
    return GatewayControlService(
        lease_handler=handler,
        lease_store=store,
        lease_authorizer=AsyncMock(return_value=SimpleNamespace(allowed=True, reason="")),
    )


def _request(capabilities: dict[str, object]) -> control_pb2.LeaseRequest:
    """带 metrics：心跳视图只在 metrics 存在时才写，缺了它"没写心跳"的断言会自动成立。"""
    return control_pb2.LeaseRequest(
        worker_id="worker-1",
        metrics=common_pb2.Metrics(max_concurrent_tasks=1),
        capabilities=encode_capabilities(capabilities),
    )


@pytest.mark.asyncio
async def test_legacy_worker_lease_is_revoked_with_an_upgrade_reason(monkeypatch) -> None:
    store = _store()
    handler = MagicMock(handle=AsyncMock(return_value=True))
    service = _service(monkeypatch, store, handler)

    response = await service.Lease(_request(LEGACY_WORKER_CAPABILITIES), MagicMock())

    assert response.revoked is True
    assert response.lease_id == ""
    assert "升级" in response.revoke_reason


@pytest.mark.asyncio
async def test_legacy_worker_never_gets_a_heartbeat_written(monkeypatch) -> None:
    """心跳是控制台"在线"的唯一来源；不写心跳，错配就不可能伪装成健康。"""
    store = _store()
    handler = MagicMock(handle=AsyncMock(return_value=True))
    service = _service(monkeypatch, store, handler)

    await service.Lease(_request(LEGACY_WORKER_CAPABILITIES), MagicMock())

    handler.handle.assert_not_awaited()
    store.grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_response_still_carries_authoritative_lease_timing(monkeypatch) -> None:
    """撤销响应也必须带权威时序，否则新 Worker 的续期不变量校验会退化成本地假设。"""
    service = _service(monkeypatch, _store(), MagicMock(handle=AsyncMock(return_value=True)))

    response = await service.Lease(_request(LEGACY_WORKER_CAPABILITIES), MagicMock())

    assert response.ttl_ms == LEASE_TTL_MS
    assert response.renew_after_ms == LEASE_RENEW_AFTER_MS


@pytest.mark.asyncio
async def test_worker_newer_than_the_gateway_is_revoked(monkeypatch) -> None:
    store = _store()
    service = _service(monkeypatch, store, MagicMock(handle=AsyncMock(return_value=True)))

    response = await service.Lease(_request({WIRE_CONTRACT_CAPABILITY: FUTURE_CONTRACT_VERSION}), MagicMock())

    assert response.revoked is True
    assert "控制面" in response.revoke_reason
    store.grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_contract_is_granted_and_persists_the_heartbeat(monkeypatch) -> None:
    store = _store()
    handler = MagicMock(handle=AsyncMock(return_value=True))
    service = _service(monkeypatch, store, handler)

    response = await service.Lease(_request(wire_contract_capability()), MagicMock())

    assert response.revoked is False
    assert response.lease_id == "lease-1"
    handler.handle.assert_awaited_once()
