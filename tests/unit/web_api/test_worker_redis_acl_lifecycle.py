from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import workers
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_direct_registration_rejects_legacy_proof_when_acl_enabled(monkeypatch) -> None:
    monkeypatch.setattr(workers.settings, "REDIS_URL", "redis://configured")
    monkeypatch.setattr(workers.settings, "REDIS_ACL_ENABLED", True)
    get_redis = AsyncMock()
    register_worker = AsyncMock()
    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.get_redis_client",
        get_redis,
    )
    monkeypatch.setattr(
        workers.worker_service,
        "register_direct_worker",
        register_worker,
    )
    request = workers.WorkerRegisterDirectRequest(worker_id="worker-1", proof="proof")

    with pytest.raises(HTTPException) as exc_info:
        await workers.register_direct_worker(request)

    assert exc_info.value.status_code == 409
    assert "安装 Key" in exc_info.value.detail
    get_redis.assert_not_awaited()
    register_worker.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_registration_remains_available_without_acl(monkeypatch) -> None:
    redis = AsyncMock()
    redis.getdel.return_value = "proof"
    worker = SimpleNamespace(public_id="worker-1")
    monkeypatch.setattr(workers.settings, "REDIS_URL", "redis://configured")
    monkeypatch.setattr(workers.settings, "REDIS_ACL_ENABLED", False)
    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.get_redis_client",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr(
        workers.worker_service,
        "register_direct_worker",
        AsyncMock(return_value=(worker, True)),
    )
    request = workers.WorkerRegisterDirectRequest(worker_id="worker-1", proof="proof")

    response = await workers.register_direct_worker(request)

    assert response.data.worker_id == "worker-1"
    assert response.data.redis_username is None
    assert response.data.redis_password is None


@pytest.mark.asyncio
async def test_acl_issue_rejects_path_identity_mismatch() -> None:
    worker = SimpleNamespace(public_id="worker-1")

    with pytest.raises(HTTPException) as exc_info:
        await workers.issue_worker_redis_acl(
            "worker-2",
            auth_context={"worker": worker},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_acl_issue_rotates_signed_worker_credentials(monkeypatch) -> None:
    redis = AsyncMock()
    worker = SimpleNamespace(
        public_id="worker-1",
        redis_username="worker_worker-1",
        transport_mode="direct",
        save=AsyncMock(),
    )
    monkeypatch.setattr(workers.settings, "REDIS_ACL_ENABLED", True)
    monkeypatch.setattr(workers, "_is_registration_acknowledged", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.get_redis_client",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr(
        "antcode_core.common.security.redis_acl.ensure_worker_acl",
        AsyncMock(return_value="rotated-password"),
    )

    response = await workers.issue_worker_redis_acl(
        "worker-1",
        auth_context={"worker": worker},
    )

    assert response.data["redis_password"] == "rotated-password"
    assert worker.transport_mode == "direct"
    worker.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_acl_issue_rejects_unacknowledged_registration(monkeypatch) -> None:
    worker = SimpleNamespace(public_id="worker-1", transport_mode="direct")
    get_redis = AsyncMock()
    ensure_acl = AsyncMock()
    monkeypatch.setattr(workers.settings, "REDIS_ACL_ENABLED", True)
    monkeypatch.setattr(workers, "_is_registration_acknowledged", AsyncMock(return_value=False))
    monkeypatch.setattr("antcode_core.infrastructure.redis.get_redis_client", get_redis)
    monkeypatch.setattr("antcode_core.common.security.redis_acl.ensure_worker_acl", ensure_acl)

    with pytest.raises(HTTPException) as exc_info:
        await workers.issue_worker_redis_acl(
            "worker-1",
            auth_context={"worker": worker},
        )

    assert exc_info.value.status_code == 409
    assert "ACK" in exc_info.value.detail
    get_redis.assert_not_awaited()
    ensure_acl.assert_not_awaited()


@pytest.mark.asyncio
async def test_acl_issue_rejects_gateway_worker_without_rotating(monkeypatch) -> None:
    worker = SimpleNamespace(public_id="worker-1", transport_mode="gateway")
    get_redis = AsyncMock()
    ensure_acl = AsyncMock()
    monkeypatch.setattr(workers.settings, "REDIS_ACL_ENABLED", True)
    monkeypatch.setattr("antcode_core.infrastructure.redis.get_redis_client", get_redis)
    monkeypatch.setattr("antcode_core.common.security.redis_acl.ensure_worker_acl", ensure_acl)

    with pytest.raises(HTTPException) as exc_info:
        await workers.issue_worker_redis_acl(
            "worker-1",
            auth_context={"worker": worker},
        )

    assert exc_info.value.status_code == 403
    assert "Direct" in exc_info.value.detail
    get_redis.assert_not_awaited()
    ensure_acl.assert_not_awaited()
