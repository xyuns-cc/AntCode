import json
from types import SimpleNamespace

import httpx
import pytest
from antcode_worker.app import wiring
from antcode_worker.services.credential import CredentialService
from antcode_worker.services.credential.persistent_store import PersistentCredentialStore


class _RegistrationClient:
    payloads: list[dict] = []

    def __init__(self, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, url, *, content, headers=None):
        payload = json.loads(content)
        if url.endswith("register-by-key-v2"):
            return self._registration_response(payload)
        assert headers and headers["X-Signature"]
        return httpx.Response(200, json={"success": True, "data": {}})

    def _registration_response(self, payload: dict) -> httpx.Response:
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            raise httpx.ReadTimeout("response lost")
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
                    "recovered": True,
                    "recovery_expires_at": "2026-07-17T12:00:00+00:00",
                },
            },
        )


@pytest.mark.parametrize("empty_environment", [False, True])
def test_restart_recovers_from_durable_intent_without_external_install_key(
    monkeypatch,
    tmp_path,
    empty_environment: bool,
) -> None:
    config = _worker_config()
    service = CredentialService(PersistentCredentialStore(tmp_path))
    _RegistrationClient.payloads = []
    monkeypatch.setattr(httpx, "Client", _RegistrationClient)
    if empty_environment:
        monkeypatch.setenv("ANTCODE_WORKER_KEY", "")
    else:
        monkeypatch.delenv("ANTCODE_WORKER_KEY", raising=False)

    with pytest.raises(httpx.ReadTimeout, match="response lost"):
        wiring._register_by_install_key(config, service)
    config.worker_key = ""
    credentials = wiring._register_by_install_key(config, service)

    first, recovered = _RegistrationClient.payloads
    assert credentials.worker_id == "worker-001"
    assert recovered["key"] == "install-key"
    assert recovered["registration_id"] == first["registration_id"]
    assert recovered["recovery_secret"] == first["recovery_secret"]
    assert recovered["client_nonce"] != first["client_nonce"]


def _worker_config() -> SimpleNamespace:
    return SimpleNamespace(
        worker_key="install-key",
        gateway_host="gateway.example.com",
        gateway_port=50051,
        api_base_url="https://control.example.com",
        host="10.0.0.2",
        name="Worker-001",
        port=8001,
        region="",
        transport_mode="gateway",
    )
