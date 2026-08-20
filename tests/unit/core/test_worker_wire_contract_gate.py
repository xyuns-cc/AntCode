"""Lease 签发前的线协议契约门禁。

锁住的不变量：**版本错配的 Worker 拿不到 Lease**。Lease 是唯一的存活信号，
拿不到就没有心跳（控制台判离线）、没有能力快照（派发前置条件不成立），
所以"控制台绿灯 + 零产出"这种静默失效在结构上不可能再出现。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts.wire_contract import (
    LEGACY_WIRE_CONTRACT_VERSION,
    MIN_SUPPORTED_WORKER_WIRE_CONTRACT,
    WIRE_CONTRACT_CAPABILITY,
    WORKER_WIRE_CONTRACT_VERSION,
    WorkerWireContractError,
    declared_wire_contract,
    require_supported_wire_contract,
    wire_contract_capability,
)
from antcode_core.application.services.workers.worker_lease_issuance import (
    WorkerLeaseGrantRequest,
    grant_authorized_worker_lease,
)

# origin/main(HEAD) 的 Worker 上报的能力快照形态：没有 wire_contract 这个键。
LEGACY_WORKER_CAPABILITIES = {"curl_cffi": {"enabled": True}, "task_types": ["code", "spider"]}
FUTURE_CONTRACT_VERSION = WORKER_WIRE_CONTRACT_VERSION + 1
#: 运行时控制失败回包还不带结构化 error_code 的那一版；控制面现在对缺码 fail-closed，
#: 放它进来的后果是每一次运行时管理失败都退化成一句"回包损坏"。
CODELESS_RUNTIME_FAILURE_CONTRACT_VERSION = 2


def _store() -> MagicMock:
    lease = SimpleNamespace(worker_id="worker-1", lease_id="lease-1", expires_at_ms=30_000)
    return MagicMock(grant=AsyncMock(return_value=lease), revoke=AsyncMock(return_value=True))


def _allowing_authorizer() -> AsyncMock:
    return AsyncMock(return_value=SimpleNamespace(allowed=True, reason=""))


def test_missing_capability_is_read_as_the_legacy_contract() -> None:
    assert declared_wire_contract(LEGACY_WORKER_CAPABILITIES) == LEGACY_WIRE_CONTRACT_VERSION
    assert declared_wire_contract(None) == LEGACY_WIRE_CONTRACT_VERSION


def test_legacy_worker_snapshot_is_rejected_with_an_actionable_reason() -> None:
    with pytest.raises(WorkerWireContractError) as exc:
        require_supported_wire_contract(LEGACY_WORKER_CAPABILITIES)

    message = str(exc.value)
    assert f"v{LEGACY_WIRE_CONTRACT_VERSION}" in message
    assert f"v{MIN_SUPPORTED_WORKER_WIRE_CONTRACT}" in message
    # 排障者必须能从这一行知道"要做什么"，而不是只知道"被拒了"。
    assert "升级" in message


def test_newer_worker_than_the_control_plane_is_rejected_too() -> None:
    """双向门禁：本版本控制面也要挡住下一版本的 Worker，否则错配只是换了个方向。"""
    with pytest.raises(WorkerWireContractError, match="高于控制面"):
        require_supported_wire_contract({WIRE_CONTRACT_CAPABILITY: FUTURE_CONTRACT_VERSION})


@pytest.mark.parametrize("declared", [True, "2", 2.0, None.__class__])
def test_non_integer_contract_version_is_rejected(declared: object) -> None:
    with pytest.raises(WorkerWireContractError, match="不是整数版本号"):
        require_supported_wire_contract({WIRE_CONTRACT_CAPABILITY: declared})


def test_current_contract_is_accepted() -> None:
    assert require_supported_wire_contract(wire_contract_capability()) == WORKER_WIRE_CONTRACT_VERSION


def test_worker_without_structured_runtime_failure_codes_is_rejected() -> None:
    """本轮 wire 断裂：失败回包必须带 error_code，上一版 Worker 拿不到 Lease。"""
    with pytest.raises(WorkerWireContractError, match="过旧"):
        require_supported_wire_contract({WIRE_CONTRACT_CAPABILITY: CODELESS_RUNTIME_FAILURE_CONTRACT_VERSION})


@pytest.mark.asyncio
async def test_legacy_worker_never_reaches_the_lease_store() -> None:
    """fail-closed 必须发生在 grant 之前：签发后再撤销会留下一个可用代际窗口。"""
    store = _store()
    authorizer = _allowing_authorizer()

    with pytest.raises(WorkerWireContractError):
        await grant_authorized_worker_lease(
            store,
            authorizer,
            WorkerLeaseGrantRequest("worker-1", capabilities=LEGACY_WORKER_CAPABILITIES),
        )

    store.grant.assert_not_awaited()
    store.revoke.assert_not_awaited()
    # 生命周期权威查询也不该发生：契约不合就没有继续问 PostgreSQL 的意义。
    authorizer.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_capability_snapshot_is_rejected_like_a_legacy_worker() -> None:
    """``capabilities=None`` 与旧 Worker 等价，不能因为"没上报"就放行。"""
    store = _store()

    with pytest.raises(WorkerWireContractError):
        await grant_authorized_worker_lease(store, _allowing_authorizer(), WorkerLeaseGrantRequest("worker-1"))

    store.grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_contract_reaches_the_lease_store() -> None:
    store = _store()

    lease = await grant_authorized_worker_lease(
        store,
        _allowing_authorizer(),
        WorkerLeaseGrantRequest("worker-1", capabilities=wire_contract_capability()),
    )

    assert lease.lease_id == "lease-1"
    store.grant.assert_awaited_once()
