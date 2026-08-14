"""Direct 传输的线协议门禁，与 Gateway 同一处签发入口、同一条 fail-closed 语义。

dev/e2e 编排用的是 ``WORKER_TRANSPORT_MODE=direct``，生产用 gateway。两条路都必须
挡住旧契约 Worker，否则门禁只覆盖了一半部署形态。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts.wire_contract import (
    WIRE_CONTRACT_CAPABILITY,
    WORKER_WIRE_CONTRACT_VERSION,
)
from antcode_core.application.services.workers.worker_lease_authority import WorkerLeaseEligibility
from antcode_web_api.routes.v1 import workers_direct_control as module
from antcode_web_api.routes.v1 import workers_direct_lease as lease_module
from fastapi import HTTPException

HTTP_UPGRADE_REQUIRED = 426
LEASE_TTL_MS = 30_000
LEASE_RENEW_AFTER_MS = 10_000
FUTURE_CONTRACT_VERSION = WORKER_WIRE_CONTRACT_VERSION + 1

# origin/main(HEAD) 的 Direct Worker 上报的 wire 形态：值是 JSON 字符串，没有 wire_contract。
LEGACY_WIRE_CAPABILITIES = {"task_types": '["code"]'}


def _worker() -> SimpleNamespace:
    return SimpleNamespace(
        public_id="worker-1",
        transport_mode="direct",
        capabilities={"task_types": ["code"]},
        id=1,
        save=AsyncMock(),
    )


def _install(monkeypatch, grant: AsyncMock) -> None:
    store = SimpleNamespace(
        policy=SimpleNamespace(renew_after_ms=LEASE_RENEW_AFTER_MS, ttl_ms=LEASE_TTL_MS),
        grant=grant,
    )
    monkeypatch.setattr(module, "_redis_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(lease_module, "LeaseStore", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(
        lease_module,
        "get_worker_lease_eligibility",
        AsyncMock(return_value=WorkerLeaseEligibility("worker-1", True, "")),
    )


def _request(capabilities: dict[str, str]):
    return module.DirectLeaseRequest(operation="lease", capabilities=capabilities)


@pytest.mark.asyncio
async def test_legacy_direct_worker_gets_upgrade_required(monkeypatch) -> None:
    grant = AsyncMock()
    _install(monkeypatch, grant)
    worker = _worker()

    with pytest.raises(HTTPException) as exc:
        await module._grant_direct_lease(worker, _request(LEGACY_WIRE_CAPABILITIES))

    assert exc.value.status_code == HTTP_UPGRADE_REQUIRED
    assert "升级" in str(exc.value.detail)
    grant.assert_not_awaited()
    # 能力投影同样不能被写：错配 Worker 不该在 PostgreSQL 里留下"可派发"的快照。
    worker.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_worker_newer_than_the_control_plane_is_rejected(monkeypatch) -> None:
    grant = AsyncMock()
    _install(monkeypatch, grant)

    with pytest.raises(HTTPException) as exc:
        await module._grant_direct_lease(
            _worker(),
            _request({WIRE_CONTRACT_CAPABILITY: str(FUTURE_CONTRACT_VERSION)}),
        )

    assert exc.value.status_code == HTTP_UPGRADE_REQUIRED
    assert "控制面" in str(exc.value.detail)
    grant.assert_not_awaited()
