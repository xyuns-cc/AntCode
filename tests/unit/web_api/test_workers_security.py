import importlib
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.security.api_key import hash_api_key
from antcode_web_api.routes.v1 import workers as workers_route
from fastapi import HTTPException, status
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _mock_run_ownership(monkeypatch):
    ownership_module = importlib.import_module("antcode_core.application.services.workers.run_ownership_service")
    monkeypatch.setattr(ownership_module, "require_worker_owns_run", AsyncMock())
    monkeypatch.setattr(ownership_module, "require_worker_owns_runs", AsyncMock())


@pytest.mark.asyncio
async def test_verify_worker_credential_headers_requires_worker_id(monkeypatch):
    async def fake_get_worker_by_id(_worker_id):
        return None

    monkeypatch.setattr(workers_route.worker_service, "get_worker_by_id", fake_get_worker_by_id)

    class DummyRequest:
        headers = {"Authorization": "Bearer token"}

    with pytest.raises(HTTPException) as exc_info:
        await workers_route._verify_worker_credential_headers(DummyRequest(), {"worker_id": ""})

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Worker 标识" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_verify_worker_credential_headers_requires_bearer(monkeypatch):
    worker = type("Worker", (), {"api_key": "expected-key"})()

    async def fake_get_worker_by_id(_worker_id):
        return worker

    monkeypatch.setattr(workers_route.worker_service, "get_worker_by_id", fake_get_worker_by_id)

    class DummyRequest:
        headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await workers_route._verify_worker_credential_headers(DummyRequest(), {"worker_id": "w-1"})

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "认证信息" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_verify_worker_credential_headers_rejects_wrong_api_key(monkeypatch):
    worker = type(
        "Worker",
        (),
        {
            "api_key_hash": hash_api_key("expected-key"),
            "api_key_previous_hash": None,
            "api_key_previous_expires_at": None,
        },
    )()

    async def fake_get_worker_by_id(_worker_id):
        return worker

    monkeypatch.setattr(workers_route.worker_service, "get_worker_by_id", fake_get_worker_by_id)

    class DummyRequest:
        headers = {"Authorization": "Bearer wrong-key"}

    with pytest.raises(HTTPException) as exc_info:
        await workers_route._verify_worker_credential_headers(DummyRequest(), {"worker_id": "w-1"})

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "API Key" in str(exc_info.value.detail)


def test_worker_report_payload_rejects_legacy_execution_id():
    with pytest.raises(ValidationError):
        workers_route.WorkerTaskLogReportRequest(
            execution_id="legacy-exec-id",
            log_type="stdout",
            content="hello",
        )


@pytest.mark.asyncio
async def test_report_task_log_uses_strict_auth_context(monkeypatch):
    append_mock = AsyncMock()

    dummy_service = SimpleNamespace(append_log=append_mock)
    log_service_module = importlib.import_module("antcode_core.application.services.workers.distributed_log_service")
    monkeypatch.setattr(log_service_module, "distributed_log_service", dummy_service)

    payload = workers_route.WorkerTaskLogReportRequest(
        run_id="run-1",
        log_type="stdout",
        content="hello",
    )
    response = await workers_route.report_task_log(payload, auth_context={"worker": object()})

    assert response.success is True
    append_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_install_key_uses_request_source_as_default_allowed_source(monkeypatch):
    class DummyUser:
        is_admin = True

    class DummyInstallKey:
        key = "KEY001"
        expires_at = datetime.now(UTC) + timedelta(hours=1)

    async def fake_get_user(**_kwargs):
        return DummyUser()

    async def fake_create_install_key(**_kwargs):
        return DummyInstallKey()

    class DummyRedis:
        def __init__(self):
            self.writes = []

        async def set(self, key, value, ex=None, nx=False):
            self.writes.append((key, value, ex, nx))
            return True

    redis = DummyRedis()

    monkeypatch.setattr(workers_route, "get_redis_client", AsyncMock(return_value=redis))

    models_module = importlib.import_module("antcode_core.domain.models")
    monkeypatch.setattr(models_module.User, "get_or_none", fake_get_user)
    monkeypatch.setattr(models_module.WorkerInstallKey, "create_install_key", fake_create_install_key)

    request = workers_route.WorkerInstallKeyRequest(os_type="linux")
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"))
    current_user = SimpleNamespace(user_id=1)

    response = await workers_route.generate_install_key(request, http_request, current_user)

    assert response.success is True
    assert response.data.allowed_source is None
    assert redis.writes


@pytest.mark.asyncio
async def test_install_key_allowed_source_rejects_corrupt_metadata(monkeypatch):
    class DummyRedis:
        async def get(self, _key):
            return b"\xff{invalid"

    monkeypatch.setattr(workers_route, "get_redis_client", AsyncMock(return_value=DummyRedis()))

    with pytest.raises(ValueError, match="install key metadata"):
        await workers_route._get_install_key_allowed_source("KEY001")


@pytest.mark.asyncio
async def test_register_worker_by_key_rejects_source_outside_allowed(monkeypatch):
    async def fake_check_global_block():
        return False, 0

    async def fake_check_blocked(_key, _source):
        return False, 0

    async def fake_get_allowed_source(_key):
        return "192.168.1.10"

    async def fake_record_failed(_key, _source):
        return 1

    async def fake_record_global_failure():
        return 1

    async def fake_get_install_key(**_kwargs):
        return SimpleNamespace(
            key="K1",
            status="pending",
            created_by=1,
            is_valid=lambda: True,
        )

    monkeypatch.setattr(workers_route, "_check_install_key_global_block", fake_check_global_block)
    monkeypatch.setattr(workers_route, "_check_install_key_blocked", fake_check_blocked)
    monkeypatch.setattr(workers_route, "_get_install_key_allowed_source", fake_get_allowed_source)
    monkeypatch.setattr(workers_route, "_record_install_key_failed_attempt", fake_record_failed)
    monkeypatch.setattr(workers_route, "_record_install_key_global_failure", fake_record_global_failure)

    models_module = importlib.import_module("antcode_core.domain.models")
    monkeypatch.setattr(models_module.WorkerInstallKey, "get_or_none", fake_get_install_key)

    request = workers_route.WorkerRegisterByKeyRequest(
        key="K1",
        name="worker",
        host="10.0.0.2",
        port=8001,
        region="local",
        client_timestamp=int(time.time()),
        client_nonce="abcde12345",
    )
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.99"))

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.register_worker_by_key(request, http_request)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "来源" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_register_worker_by_key_rejects_replay_claim(monkeypatch):
    async def fake_check_global_block():
        return False, 0

    async def fake_check_blocked(_key, _source):
        return False, 0

    async def fake_get_allowed_source(_key):
        return ""

    async def fake_claim(*_args, **_kwargs):
        return False, "请求重复（nonce 已使用）"

    async def fake_record_failed(_key, _source):
        return 1

    async def fake_record_global_failure():
        return 1

    async def fake_get_install_key(**_kwargs):
        return SimpleNamespace(
            key="K1",
            status="pending",
            created_by=1,
            is_valid=lambda: True,
        )

    monkeypatch.setattr(workers_route, "_check_install_key_global_block", fake_check_global_block)
    monkeypatch.setattr(workers_route, "_check_install_key_blocked", fake_check_blocked)
    monkeypatch.setattr(workers_route, "_get_install_key_allowed_source", fake_get_allowed_source)
    monkeypatch.setattr(workers_route, "_claim_install_key_source_once", fake_claim)
    monkeypatch.setattr(workers_route, "_record_install_key_failed_attempt", fake_record_failed)
    monkeypatch.setattr(workers_route, "_record_install_key_global_failure", fake_record_global_failure)

    models_module = importlib.import_module("antcode_core.domain.models")
    monkeypatch.setattr(models_module.WorkerInstallKey, "get_or_none", fake_get_install_key)

    request = workers_route.WorkerRegisterByKeyRequest(
        key="K1",
        name="worker",
        host="10.0.0.2",
        port=8001,
        region="local",
        client_timestamp=int(time.time()),
        client_nonce="abcde12345",
    )
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.2"))

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.register_worker_by_key(request, http_request)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_register_worker_by_key_rejects_when_blocked(monkeypatch):
    async def fake_check_global_block():
        return False, 0

    async def fake_check_blocked(_key, _source):
        return True, 120

    monkeypatch.setattr(workers_route, "_check_install_key_global_block", fake_check_global_block)
    monkeypatch.setattr(workers_route, "_check_install_key_blocked", fake_check_blocked)

    request = workers_route.WorkerRegisterByKeyRequest(
        key="K1",
        name="worker",
        host="10.0.0.2",
        port=8001,
        region="local",
        client_timestamp=int(time.time()),
        client_nonce="abcde12345",
    )
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.2"))

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.register_worker_by_key(request, http_request)

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_report_task_logs_batch_exposes_partial_failure(monkeypatch):
    async def fake_append_logs(run_id, log_type, contents):
        _ = log_type
        if run_id == "run-fail":
            raise RuntimeError("boom")
        return contents

    dummy_service = SimpleNamespace(append_logs=fake_append_logs)
    log_service_module = importlib.import_module("antcode_core.application.services.workers.distributed_log_service")
    monkeypatch.setattr(log_service_module, "distributed_log_service", dummy_service)

    payload = workers_route.WorkerTaskLogsBatchReportRequest(
        logs=[
            workers_route.WorkerTaskLogReportRequest(
                run_id="run-1",
                log_type="stdout",
                content="ok1",
            ),
            workers_route.WorkerTaskLogReportRequest(
                run_id="run-fail",
                log_type="stderr",
                content="bad",
            ),
            workers_route.WorkerTaskLogReportRequest(
                run_id="run-2",
                log_type="stdout",
                content="ok2",
            ),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.report_task_logs_batch(payload, auth_context={"worker": object()})

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_report_execution_heartbeat_uses_run_id(monkeypatch):
    update_heartbeat_mock = AsyncMock(return_value=True)
    persistence_module = importlib.import_module("antcode_core.application.services.scheduler.task_persistence")
    monkeypatch.setattr(
        persistence_module.task_persistence_service,
        "update_heartbeat",
        update_heartbeat_mock,
    )

    payload = workers_route.WorkerTaskHeartbeatReportRequest(run_id="run-heartbeat-1")
    response = await workers_route.report_execution_heartbeat(
        payload,
        auth_context={"worker": object()},
    )

    assert response.success is True
    update_heartbeat_mock.assert_awaited_once_with("run-heartbeat-1")


@pytest.mark.asyncio
async def test_report_task_status_uses_run_id(monkeypatch):
    update_task_status_mock = AsyncMock()
    log_service_module = importlib.import_module("antcode_core.application.services.workers.distributed_log_service")
    monkeypatch.setattr(
        log_service_module.distributed_log_service,
        "update_task_status",
        update_task_status_mock,
    )

    payload = workers_route.WorkerTaskStatusReportRequest(
        run_id="run-status-1",
        status="success",
        exit_code=0,
        error_message="",
    )
    response = await workers_route.report_task_status(
        payload,
        auth_context={"worker": object()},
    )

    assert response.success is True
    update_task_status_mock.assert_awaited_once_with(
        "run-status-1",
        "success",
        exit_code=0,
        error_message="",
    )


@pytest.mark.asyncio
async def test_get_best_worker_forwards_require_render(monkeypatch):
    from antcode_core.application.services.workers import worker_load_balancer

    select_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(worker_load_balancer, "select_best_worker", select_mock)

    response = await workers_route.get_best_worker(
        region=None,
        tags=None,
        require_render=True,
        current_user=SimpleNamespace(user_id=1),
    )

    assert response.success is True
    select_mock.assert_awaited_once_with(region=None, tags=None, require_render=True)
