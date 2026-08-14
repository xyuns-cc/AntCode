import contextlib
import json
from types import SimpleNamespace

import httpx
import pytest
from antcode_core.common.security import verify_hmac_signature
from antcode_core.common.utils.worker_request import HTTP_POST_METHOD, request_path_from_url
from antcode_worker.app import wiring
from antcode_worker.services.credential.service import WorkerCredentials


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
    credential_service = SimpleNamespace(
        ensure_durable_writable=lambda: None,
        registration_session=lambda: contextlib.nullcontext(),
        load=lambda: initial,
        save=lambda value: saved.append(value) or True,
    )
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
