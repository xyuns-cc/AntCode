import contextlib
import json
from types import SimpleNamespace

import httpx
import pytest
from antcode_core.common.security import verify_hmac_signature
from antcode_core.common.security.worker_auth_reasons import WorkerAuthReason
from antcode_core.common.utils.worker_request import HTTP_POST_METHOD, request_path_from_url
from antcode_worker.app import wiring
from antcode_worker.app.control_plane_rejection import ControlPlaneIdentityUnknown
from antcode_worker.services.credential.service import WorkerCredentials

_CREDENTIAL_PATH = "/app/data/worker/secrets/worker_credentials.json"


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


def _credential_service(initial: WorkerCredentials, saved: list[WorkerCredentials]) -> SimpleNamespace:
    return SimpleNamespace(
        ensure_durable_writable=lambda: None,
        registration_session=lambda: contextlib.nullcontext(),
        load=lambda: initial,
        save=lambda value: saved.append(value) or True,
        store=SimpleNamespace(describe_location=lambda: _CREDENTIAL_PATH),
    )


def _rejecting_client(monkeypatch, status_code: int, body: dict) -> None:
    response = httpx.Response(status_code, json=body)

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url, *, content, headers):
            return response

    monkeypatch.setattr(httpx, "Client", Client)


def test_direct_acl_issue_is_signed_and_persisted_as_one_credential(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "redis_username": "worker_worker-001",
                "redis_password": "rotated-password",
            },
        },
    )
    captured: dict = {}

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url, *, content, headers):
            captured.update(url=url, body=content, headers=headers)
            return response

    saved: list[WorkerCredentials] = []
    initial = _credentials()
    credential_service = _credential_service(initial, saved)
    config = SimpleNamespace(
        gateway_host="gateway.example.com",
        api_base_url="https://control.example.com",
    )
    monkeypatch.setattr(httpx, "Client", Client)

    updated = wiring._issue_direct_redis_acl(
        config=config,
        credentials=initial,
        credential_service=credential_service,
    )

    assert captured["url"].endswith("/workers/worker-001/redis-acl/issue")
    assert captured["headers"]["Authorization"] == "Bearer api-worker-001"
    assert captured["headers"]["X-Worker-ID"] == "worker-001"
    assert verify_hmac_signature(
        captured["body"],
        initial.secret_key,
        method=HTTP_POST_METHOD,
        path=request_path_from_url(captured["url"]),
        signature=captured["headers"]["X-Signature"],
        timestamp=int(captured["headers"]["X-Timestamp"]),
        nonce=captured["headers"]["X-Nonce"],
        version=captured["headers"]["X-Signature-Version"],
    )
    assert json.loads(captured["body"]) == {}
    assert updated.redis_username == "worker_worker-001"
    assert saved == [updated]


def test_direct_acl_issue_does_not_send_when_store_is_not_durable(monkeypatch) -> None:
    def reject_client(**_kwargs):
        raise AssertionError("持久化预检失败后不得创建 HTTP 客户端")

    def reject_storage() -> None:
        raise OSError("disk is read-only")

    credential_service = SimpleNamespace(ensure_durable_writable=reject_storage)
    config = SimpleNamespace(gateway_host="gateway.example.com")
    monkeypatch.setattr(httpx, "Client", reject_client)

    with pytest.raises(OSError, match="disk is read-only"):
        wiring._issue_direct_redis_acl(
            config=config,
            credentials=_credentials(),
            credential_service=credential_service,
        )


def _issue_against_rejection(monkeypatch, error_code: str, message: str):
    """走真实的 ``_issue_direct_redis_acl`` 调用链，只把 HTTP 回包换成控制面的拒绝。"""
    _rejecting_client(monkeypatch, 401, {"success": False, "message": message, "data": {"error_code": error_code}})
    saved: list[WorkerCredentials] = []
    initial = _credentials()
    config = SimpleNamespace(gateway_host="gateway.example.com", api_base_url="https://control.example.com")
    with pytest.raises(RuntimeError) as exc_info:
        wiring._issue_direct_redis_acl(
            config=config,
            credentials=initial,
            credential_service=_credential_service(initial, saved),
        )
    return exc_info.value, saved


def test_control_plane_identity_loss_names_the_credential_file_to_clear(monkeypatch) -> None:
    """库被重建后旧凭据结构合法但已失效：报错必须指到要清的那一份，不能只说"签名验证失败"。"""
    error, saved = _issue_against_rejection(
        monkeypatch,
        WorkerAuthReason.IDENTITY_UNKNOWN.value,
        "控制面不认识该 Worker 身份：库中没有这条记录",
    )

    assert isinstance(error, ControlPlaneIdentityUnknown)
    assert _CREDENTIAL_PATH in str(error)
    assert "重新注册" in str(error)
    # 撤销不能被打穿：被拒之后既不自动换身份，也不覆写本地凭据。
    assert saved == []


def test_control_plane_signature_rejection_is_not_reported_as_identity_loss(monkeypatch) -> None:
    """反面：真的签名不符不得被翻译成"清凭据重新注册"，那会白毁一份正常身份。"""
    error, saved = _issue_against_rejection(
        monkeypatch,
        WorkerAuthReason.SIGNATURE_INVALID.value,
        "HMAC 签名与请求内容不符",
    )

    assert not isinstance(error, ControlPlaneIdentityUnknown)
    assert str(error) == "HMAC 签名与请求内容不符"
    assert _CREDENTIAL_PATH not in str(error)
    assert saved == []
