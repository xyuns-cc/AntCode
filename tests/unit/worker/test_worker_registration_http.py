import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from antcode_core.common.security import verify_hmac_signature
from antcode_core.common.utils.worker_request import HTTP_POST_METHOD, request_path_from_url
from antcode_worker.app import wiring
from antcode_worker.app.worker_registration import resume_registration_ack
from antcode_worker.services.credential.persistent_store import PersistentCredentialStore
from antcode_worker.services.credential.registration_intent import RegistrationRequest
from antcode_worker.services.credential.service import CredentialService, WorkerCredentials

EXPECTED_REGISTRATION_REQUEST_COUNT = 2


def _credentials() -> WorkerCredentials:
    return WorkerCredentials(
        worker_id="worker-001",
        api_key="api-worker-001",
        secret_key="secret-worker-001",
        gateway_host="gateway.example.com",
        gateway_port=50051,
        redis_username="redis-worker-001",
        redis_password="redis-secret-worker-001",
    )


def _registration_request() -> RegistrationRequest:
    return RegistrationRequest(
        name="Worker-001",
        host="10.0.0.2",
        port=8001,
        region="",
        transport_mode="gateway",
        api_base_url="https://control.example.com",
        gateway_host="gateway.example.com",
        gateway_port=50051,
    )


def _assert_signature(url: str, body: bytes, *, headers: dict, secret: str) -> None:
    assert verify_hmac_signature(
        body,
        secret,
        method=HTTP_POST_METHOD,
        path=request_path_from_url(url),
        signature=headers["X-Signature"],
        timestamp=int(headers["X-Timestamp"]),
        nonce=headers["X-Nonce"],
        version=headers["X-Signature-Version"],
    )


def _config(transport_mode: str = "gateway") -> SimpleNamespace:
    return SimpleNamespace(
        worker_key="install-key",
        gateway_host="gateway.example.com",
        gateway_port=50051,
        api_base_url="https://control.example.com",
        host="10.0.0.2",
        name="Worker-001",
        port=8001,
        region="",
        transport_mode=transport_mode,
    )


def test_install_key_registration_fails_when_credentials_cannot_persist(monkeypatch, tmp_path: Path) -> None:
    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url, *, content, headers=None):
            payload = json.loads(content)
            assert headers == {"Content-Type": "application/json"}
            assert payload["key"] == "install-key"
            assert payload["transport_mode"] == "direct"
            return _registration_response(payload)

    store = PersistentCredentialStore(tmp_path)
    credential_service = CredentialService(store)

    def reject_save(_credentials):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", reject_save)
    monkeypatch.setattr(httpx, "Client", Client)

    with pytest.raises(OSError, match="disk full"):
        wiring._register_by_install_key(_config("direct"), credential_service)
    assert (tmp_path / "secrets" / "worker_registration_intent.json").exists()


def _registration_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "worker_id": "worker-001",
                "api_key": "api-key",
                "secret_key": "secret-key",
                "registration_id": payload["registration_id"],
                "protocol_version": 2,
                "recovered": False,
                "recovery_expires_at": "2026-07-17T12:00:00+00:00",
            },
        },
    )


def test_install_key_registration_persists_then_acknowledges(monkeypatch, tmp_path: Path) -> None:
    requests: list[tuple[str, bytes, dict | None]] = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url, *, content, headers=None):
            payload = json.loads(content)
            requests.append((url, content, headers))
            if url.endswith("register-by-key-v2"):
                return _registration_response(payload)
            return httpx.Response(200, json={"success": True, "data": {}})

    service = CredentialService(PersistentCredentialStore(tmp_path))
    monkeypatch.setattr(httpx, "Client", Client)

    credentials = wiring._register_by_install_key(_config(), service)

    assert credentials.worker_id == "worker-001"
    assert credentials.registration_id
    assert len(requests) == EXPECTED_REGISTRATION_REQUEST_COUNT
    ack_url, ack_body, ack_headers = requests[1]
    assert ack_url.endswith("/workers/worker-001/registration-ack")
    assert ack_headers is not None
    _assert_signature(ack_url, ack_body, headers=ack_headers, secret="secret-key")
    assert not (tmp_path / "secrets" / "worker_registration_intent.json").exists()


def test_restart_with_saved_credentials_only_retries_registration_ack(monkeypatch, tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    with store.registration_session("install-key", _registration_request()) as intent:
        assert intent is not None
        credentials = replace(_credentials(), registration_id=intent.registration_id)
        CredentialService(store).save(credentials)
    calls: list[str] = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url, *, content, headers=None):
            calls.append(url)
            assert json.loads(content) == {"registration_id": credentials.registration_id}
            assert headers is not None
            _assert_signature(url, content, headers=headers, secret=credentials.secret_key)
            return httpx.Response(200, json={"success": True, "data": {}})

    monkeypatch.setattr(httpx, "Client", Client)
    resume_registration_ack(CredentialService(store), credentials)

    assert calls == ["https://control.example.com/api/v1/workers/worker-001/registration-ack"]
    assert not (tmp_path / "secrets" / "worker_registration_intent.json").exists()


def test_install_key_registration_does_not_send_when_store_is_not_durable(monkeypatch) -> None:
    def reject_client(**_kwargs):
        raise AssertionError("持久化预检失败后不得创建 HTTP 客户端")

    def reject_storage() -> None:
        raise OSError("read-only filesystem")

    credential_service = SimpleNamespace(ensure_durable_writable=reject_storage)
    monkeypatch.setattr(httpx, "Client", reject_client)

    with pytest.raises(OSError, match="read-only filesystem"):
        wiring._register_by_install_key(SimpleNamespace(worker_key="install-key"), credential_service)


def test_structurally_valid_credentials_never_trigger_reregistration(monkeypatch) -> None:
    """撤销护栏（非证伪项：修复前后行为一致，此处冻结它不被后续"自动恢复"改掉）。

    管理员删除/停用一台 Worker 靠的就是把身份从控制面库里去掉。只要本地还留着一份
    结构合法的凭据，启动路径就不得拿安装 Key 自己回来——否则那条运维手段直接作废。
    """

    def reject_registration(*_args, **_kwargs):
        raise AssertionError("本地凭据结构合法时不得走安装 Key 重新注册")

    monkeypatch.setattr(wiring, "_register_by_install_key", reject_registration)
    credentials = _credentials()

    assert (
        wiring._require_control_credentials(
            _config("direct"),
            SimpleNamespace(),
            credentials,
            required=True,
        )
        is credentials
    )
