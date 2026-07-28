from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.worker_install_key import WorkerInstallKey
from antcode_core.domain.schemas.worker import WorkerRegisterByKeyV2Request, WorkerRegistrationAckRequest
from antcode_web_api.routes.v1 import worker_install
from antcode_web_api.services.worker_registration import RegistrationResult


def _request() -> WorkerRegisterByKeyV2Request:
    return WorkerRegisterByKeyV2Request(
        key="INSTALL-KEY",
        name="worker",
        host="127.0.0.1",
        client_timestamp=1,
        client_nonce="nonce-123",
        registration_id="a" * 32,
        recovery_secret="b" * 64,
    )


@pytest.mark.asyncio
async def test_v2_route_keeps_success_when_fail_counter_cleanup_fails(monkeypatch) -> None:
    install_key = SimpleNamespace(key=WorkerInstallKey.hash_plaintext("INSTALL-KEY"), allowed_source=None)
    workers_route = SimpleNamespace(
        _extract_request_source=lambda _request: "127.0.0.1",
        _check_install_key_blocked=AsyncMock(return_value=(False, 0)),
        _claim_install_key_source_once=AsyncMock(return_value=(True, "ok")),
        _record_install_key_failed_attempt=AsyncMock(),
        _clear_install_key_fail_counter=AsyncMock(side_effect=RuntimeError("redis unavailable")),
        _is_source_match=lambda _source, _rule: True,
    )
    result = RegistrationResult(
        worker_id="worker-1",
        api_key="api",
        secret_key="secret",
        registration_id="a" * 32,
        recovery_expires_at=datetime.now(UTC) + timedelta(hours=1),
        recovered=False,
    )
    monkeypatch.setattr(worker_install, "_workers_route", lambda: workers_route)
    monkeypatch.setattr(WorkerInstallKey, "find_by_plaintext", AsyncMock(return_value=install_key))
    monkeypatch.setattr(WorkerInstallKey, "matches_plaintext", classmethod(lambda cls, stored, plain: True))
    monkeypatch.setattr(worker_install, "register_or_recover", AsyncMock(return_value=result))

    response = await worker_install.register_worker_by_key_v2(_request(), SimpleNamespace())

    assert response.success is True
    assert response.data.worker_id == "worker-1"
    workers_route._clear_install_key_fail_counter.assert_awaited_once()


@pytest.mark.asyncio
async def test_registration_ack_rejects_path_identity_mismatch() -> None:
    request = WorkerRegistrationAckRequest(registration_id="a" * 32)
    auth_context = {"worker": SimpleNamespace(public_id="worker-other")}

    with pytest.raises(Exception) as exc_info:
        await worker_install.acknowledge_worker_registration("worker-1", request, auth_context)

    assert getattr(exc_info.value, "status_code", None) == 403
