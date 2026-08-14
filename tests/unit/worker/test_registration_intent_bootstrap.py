import contextlib
from pathlib import Path
from types import SimpleNamespace

from antcode_worker.app.worker_registration import register_by_install_key
from antcode_worker.services.credential.persistent_store import PersistentCredentialStore
from antcode_worker.services.credential.registration_intent import RegistrationRequest
from antcode_worker.services.credential.service import CredentialService


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        worker_key="",
        gateway_host="gateway.example.com",
        gateway_port=50051,
        api_base_url="https://control.example.com",
        host="10.0.0.2",
        name="Worker-001",
        port=8001,
        region="",
        transport_mode="gateway",
    )


def _request() -> RegistrationRequest:
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


def test_blank_install_key_environment_does_not_create_incomplete_intent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTCODE_WORKER_KEY", "")
    service = CredentialService(PersistentCredentialStore(tmp_path))

    assert register_by_install_key(_config(), service) is None
    assert not (tmp_path / "secrets" / "worker_registration_intent.json").exists()


def test_environment_install_key_passes_registration_request_snapshot(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class CredentialServiceProbe:
        def ensure_durable_writable(self) -> None:
            pass

        def registration_session(self, install_key, request):
            captured.update(install_key=install_key, request=request)
            return contextlib.nullcontext()

    monkeypatch.setenv("ANTCODE_WORKER_KEY", "install-key")

    assert register_by_install_key(_config(), CredentialServiceProbe()) is None
    assert captured == {"install_key": "install-key", "request": _request()}
