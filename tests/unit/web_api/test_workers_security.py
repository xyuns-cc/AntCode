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


class _InstallKeyRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def set(self, key: str, value: str, ex: int) -> bool:
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            deleted += int(key in self.values)
            self.values.pop(key, None)
            self.ttls.pop(key, None)
        return deleted


def test_install_key_source_uses_rightmost_untrusted_forwarded_hop(monkeypatch) -> None:
    monkeypatch.setenv("ANTCODE_TRUSTED_PROXIES", "10.0.0.0/8")
    request = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.5"),
        headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7, 10.0.0.4"},
    )

    assert workers_route._extract_request_source(request) == "198.51.100.7"


def test_install_key_source_rejects_invalid_forwarded_ip(monkeypatch) -> None:
    monkeypatch.setenv("ANTCODE_TRUSTED_PROXIES", "10.0.0.0/8")
    request = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.5"),
        headers={"X-Forwarded-For": "not-an-ip"},
    )

    with pytest.raises(HTTPException) as exc_info:
        workers_route._extract_request_source(request)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_install_key_source_does_not_fall_back_to_reported_worker_host() -> None:
    request = SimpleNamespace(client=None, headers={})

    with pytest.raises(HTTPException) as exc_info:
        workers_route._extract_request_source(request)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_invalid_source_rule_never_matches_by_string_fallback() -> None:
    assert workers_route._is_source_match("not-an-ip", "not-an-ip") is False


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


def test_worker_log_report_models_enforce_resource_limits():
    with pytest.raises(ValidationError):
        workers_route.WorkerTaskLogReportRequest(
            run_id="run-1",
            log_type="stdout",
            content="x" * (workers_route.MAX_LOG_LINE_CHARS + 1),
        )
    item = workers_route.WorkerTaskLogReportRequest(run_id="run-1", content="line")
    with pytest.raises(ValidationError):
        workers_route.WorkerTaskLogsBatchReportRequest(logs=[item] * (workers_route.MAX_LOG_BATCH_ENTRIES + 1))


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
async def test_generate_install_key_persists_explicit_allowed_source(monkeypatch):
    class DummyUser:
        is_admin = True

    class DummyInstallKey:
        # P2-11：DB 只存 hash；plaintext_key 作为内存属性供 API 首次返回
        key = "hashof-KEY001"
        expires_at = datetime.now(UTC) + timedelta(hours=1)
        plaintext_key = "KEY001"

    async def fake_get_user(**_kwargs):
        return DummyUser()

    create_kwargs = {}

    async def fake_create_install_key(**kwargs):
        create_kwargs.update(kwargs)
        return DummyInstallKey()

    class DummyRedis:
        def __init__(self):
            self.writes = []

        async def set(self, key, value, ex=None, nx=False):
            self.writes.append((key, value, ex, nx))
            return True

    redis = DummyRedis()

    monkeypatch.setattr(workers_route, "get_redis_client", AsyncMock(return_value=redis))
    install_config = SimpleNamespace(
        api_base_url="https://control.example.com",
        source_url="https://github.com/xyuns-cc/AntCode.git",
        source_ref="a" * 40,
        gateway_endpoint="gateway.example.com:50051",
        gateway_tls=True,
        uv_version="0.8.17",
    )
    monkeypatch.setattr(workers_route, "load_worker_install_config", lambda _settings: install_config)

    models_module = importlib.import_module("antcode_core.domain.models")
    monkeypatch.setattr(models_module.User, "get_or_none", fake_get_user)
    monkeypatch.setattr(models_module.WorkerInstallKey, "create_install_key", fake_create_install_key)

    request = workers_route.WorkerInstallKeyRequest(os_type="linux", allowed_source="10.0.0.0/24")
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"), headers={})
    current_user = SimpleNamespace(user_id=1)

    response = await workers_route.generate_install_key(request, http_request, current_user)

    assert response.success is True
    assert response.data.allowed_source == "10.0.0.0/24"
    assert create_kwargs["allowed_source"] == "10.0.0.0/24"
    assert redis.writes
    assert "sha256sum" in response.data.install_command
    assert "gateway.example.com:50051" in response.data.install_command


@pytest.mark.asyncio
async def test_register_uses_database_source_when_redis_meta_is_absent(monkeypatch):
    async def fake_check_blocked(_key, _source):
        return False, 0

    async def fake_record_failed(_key, _source):
        return 1

    async def fake_get_install_key(*_args, **_kwargs):
        return SimpleNamespace(
            key="K1",
            status="pending",
            created_by=1,
            allowed_source="192.168.1.10",
            is_valid=lambda: True,
        )

    monkeypatch.setattr(workers_route, "_check_install_key_blocked", fake_check_blocked)
    monkeypatch.setattr(workers_route, "_record_install_key_failed_attempt", fake_record_failed)

    models_module = importlib.import_module("antcode_core.domain.models")
    # P2-11：查找方式换成 find_by_plaintext，测试跟着换 patch 目标；
    # matches_plaintext 也 patch 成恒 True，跳过 hash 校验（本测试关注上层业务逻辑）。
    monkeypatch.setattr(models_module.WorkerInstallKey, "find_by_plaintext", fake_get_install_key)
    monkeypatch.setattr(
        models_module.WorkerInstallKey,
        "matches_plaintext",
        classmethod(lambda cls, stored, plain: True),
    )

    request = workers_route.WorkerRegisterByKeyRequest(
        key="K1",
        name="worker",
        host="10.0.0.2",
        port=8001,
        region="local",
        client_timestamp=int(time.time()),
        client_nonce="abcde12345",
    )
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.99"), headers={})

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.register_worker_by_key(request, http_request)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "来源" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_register_worker_by_key_rejects_replay_claim(monkeypatch):
    async def fake_check_blocked(_key, _source):
        return False, 0

    async def fake_claim(*_args, **_kwargs):
        return False, "请求重复（nonce 已使用）"

    async def fake_record_failed(_key, _source):
        return 1

    async def fake_get_install_key(*_args, **_kwargs):
        return SimpleNamespace(
            key="K1",
            status="pending",
            created_by=1,
            allowed_source=None,
            is_valid=lambda: True,
        )

    monkeypatch.setattr(workers_route, "_check_install_key_blocked", fake_check_blocked)
    monkeypatch.setattr(workers_route, "_claim_install_key_source_once", fake_claim)
    monkeypatch.setattr(workers_route, "_record_install_key_failed_attempt", fake_record_failed)

    models_module = importlib.import_module("antcode_core.domain.models")
    # P2-11：查找方式换成 find_by_plaintext，测试跟着换 patch 目标；
    # matches_plaintext 也 patch 成恒 True，跳过 hash 校验（本测试关注上层业务逻辑）。
    monkeypatch.setattr(models_module.WorkerInstallKey, "find_by_plaintext", fake_get_install_key)
    monkeypatch.setattr(
        models_module.WorkerInstallKey,
        "matches_plaintext",
        classmethod(lambda cls, stored, plain: True),
    )

    request = workers_route.WorkerRegisterByKeyRequest(
        key="K1",
        name="worker",
        host="10.0.0.2",
        port=8001,
        region="local",
        client_timestamp=int(time.time()),
        client_nonce="abcde12345",
    )
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.2"), headers={})

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.register_worker_by_key(request, http_request)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_register_worker_by_key_rejects_when_blocked(monkeypatch):
    async def fake_check_blocked(_key, _source):
        return True, 120

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
    http_request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.2"), headers={})

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.register_worker_by_key(request, http_request)

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_register_worker_by_key_rejects_oversized_install_key() -> None:
    with pytest.raises(ValidationError):
        workers_route.WorkerRegisterByKeyRequest(
            key="K" * 65,
            name="worker",
            host="10.0.0.2",
            port=8001,
            region="local",
            client_timestamp=int(time.time()),
            client_nonce="abcde12345",
        )


@pytest.mark.asyncio
async def test_install_key_failure_threshold_only_blocks_attacker_source(monkeypatch) -> None:
    redis = _InstallKeyRedis()
    monkeypatch.setattr(workers_route, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(workers_route.settings, "WORKER_INSTALL_KEY_FAIL_THRESHOLD", 3)
    monkeypatch.setattr(workers_route.settings, "WORKER_INSTALL_KEY_BLOCK_SECONDS", 600)

    for index in range(3):
        await workers_route._record_install_key_failed_attempt(
            f"invalid-key-{index}",
            "198.51.100.10",
        )

    attacker_blocked, attacker_ttl = await workers_route._check_install_key_blocked(
        "another-invalid-key",
        "198.51.100.10",
    )
    unrelated_blocked, unrelated_ttl = await workers_route._check_install_key_blocked(
        "valid-key",
        "203.0.113.20",
    )

    assert (attacker_blocked, attacker_ttl) == (True, 600)
    assert (unrelated_blocked, unrelated_ttl) == (False, 0)
    assert not hasattr(workers_route, "_check_install_key_global_block")
    assert all("global" not in key for key in redis.values.keys())


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

    # P1-round6 5.2: 部分失败改返回 207 Multi-Status 携带 failed_groups, 让 Worker
    # 只重试失败组;原 503 全体重试会让已成功组入库复制(sequence 递增)。
    assert exc_info.value.status_code == status.HTTP_207_MULTI_STATUS
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["received"] > 0
    assert detail["total"] == 3
    assert len(detail["failed_groups"]) == 1
    assert detail["failed_groups"][0]["run_id"] == "run-fail"
    assert detail["failed_groups"][0]["log_type"] == "stderr"


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
