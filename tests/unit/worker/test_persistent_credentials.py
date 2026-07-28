import contextlib
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from antcode_worker.app import wiring
from antcode_worker.app.worker_registration import (
    _should_trust_env_proxy,
    normalize_api_base_url,
    resume_registration_ack,
)
from antcode_worker.config import WorkerConfig
from antcode_worker.services.credential.env_store import EnvCredentialStore
from antcode_worker.services.credential.persistent_store import PersistentCredentialStore
from antcode_worker.services.credential.registration_intent import RegistrationRequest
from antcode_worker.services.credential.service import CredentialService, WorkerCredentials


def _credentials(worker_id: str = "worker-001") -> WorkerCredentials:
    return WorkerCredentials(
        worker_id=worker_id,
        api_key=f"api-{worker_id}",
        secret_key=f"secret-{worker_id}",
        gateway_host="gateway.example.com",
        gateway_port=50051,
        redis_username=f"redis-{worker_id}",
        redis_password=f"redis-secret-{worker_id}",
    )


def _clear_credential_env(monkeypatch) -> None:
    for env_name in (
        "WORKER_CREDENTIAL_WORKER_ID",
        "WORKER_CREDENTIAL_API_KEY",
        "WORKER_CREDENTIAL_SECRET_KEY",
        "WORKER_CREDENTIAL_GATEWAY_HOST",
        "WORKER_CREDENTIAL_GATEWAY_PORT",
        "WORKER_CREDENTIAL_REGISTERED_AT",
    ):
        monkeypatch.delenv(env_name, raising=False)


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


@pytest.mark.parametrize(
    "url",
    [
        "http://control.example.com:8000",
        "http://192.168.1.250:8000",
        "control.example.com:8000",
    ],
)
def test_remote_control_api_requires_https(url: str) -> None:
    with pytest.raises(ValueError, match="必须使用 HTTPS"):
        normalize_api_base_url(url, "control.example.com")


def test_explicit_internal_http_allows_only_single_label_service() -> None:
    assert (
        normalize_api_base_url(
            "http://web-api:8000",
            "web-api",
            allow_insecure_internal=True,
        )
        == "http://web-api:8000"
    )
    with pytest.raises(ValueError, match="必须使用 HTTPS"):
        normalize_api_base_url(
            "http://web-api.internal:8000",
            "web-api.internal",
            allow_insecure_internal=True,
        )


def test_internal_control_service_bypasses_environment_proxy() -> None:
    assert _should_trust_env_proxy("http://web-api:8000") is False
    assert _should_trust_env_proxy("https://control.example.com") is True


def test_persistent_credentials_survive_restart_and_precede_environment(monkeypatch, tmp_path: Path) -> None:
    _clear_credential_env(monkeypatch)
    first_service = CredentialService(PersistentCredentialStore(tmp_path))
    assert first_service.save(_credentials("file-worker"))
    credential_path = tmp_path / "secrets" / "worker_credentials.json"
    assert stat.S_IMODE(credential_path.stat().st_mode) == 0o600
    monkeypatch.setenv("WORKER_CREDENTIAL_WORKER_ID", "env-worker")
    monkeypatch.setenv("WORKER_CREDENTIAL_API_KEY", "env-api")
    monkeypatch.setenv("WORKER_CREDENTIAL_SECRET_KEY", "env-secret")

    restarted = CredentialService(PersistentCredentialStore(tmp_path)).load()

    assert restarted is not None
    assert restarted.worker_id == "file-worker"
    assert restarted.api_key == "api-file-worker"
    assert restarted.redis_username == "redis-file-worker"
    assert restarted.redis_password == "redis-secret-file-worker"


def test_atomic_write_failure_preserves_previous_credentials(monkeypatch, tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    assert store.save(_credentials("original").to_dict())

    def fail_replace(_source, _target, **_kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr("antcode_worker.services.credential.persistent_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        store.save(_credentials("replacement").to_dict())

    assert store.load()["worker_id"] == "original"
    assert not list((tmp_path / "secrets").glob(".worker_credentials.json.*"))


def test_persistent_store_rejects_path_escape(monkeypatch, tmp_path: Path) -> None:
    _clear_credential_env(monkeypatch)
    data_root = tmp_path / "data"
    outside = tmp_path / "outside"
    data_root.mkdir()
    outside.mkdir()
    (data_root / "secrets").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="越过 data root"):
        PersistentCredentialStore(data_root).load()


def test_invalid_file_permissions_do_not_fall_back_to_environment(monkeypatch, tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    assert store.save(_credentials("file-worker").to_dict())
    os.chmod(store.path, 0o644)
    monkeypatch.setenv("WORKER_CREDENTIAL_WORKER_ID", "env-worker")
    monkeypatch.setenv("WORKER_CREDENTIAL_API_KEY", "env-api")
    monkeypatch.setenv("WORKER_CREDENTIAL_SECRET_KEY", "env-secret")

    with pytest.raises(PermissionError, match="0600"):
        store.load()


@pytest.mark.skipif(os.name == "nt", reason="Unix hard-link invariant")
def test_persistent_store_rejects_hardlinked_credential_file(tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    assert store.save(_credentials("file-worker").to_dict())
    os.link(store.path, tmp_path / "credential-copy.json")

    with pytest.raises(PermissionError, match="硬链接"):
        store.load()


def test_persistent_store_rejects_symlinked_credential_file(tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    assert store.save(_credentials("file-worker").to_dict())
    outside = tmp_path / "outside.json"
    outside.write_text(store.path.read_text(encoding="utf-8"), encoding="utf-8")
    store.path.unlink()
    store.path.symlink_to(outside)

    with pytest.raises(ValueError, match="符号链接"):
        store.load()


def test_persistent_store_rejects_oversized_credentials(tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    payload = _credentials("file-worker").to_dict()
    payload["api_key"] = "x" * (64 * 1024)

    with pytest.raises(ValueError, match="64 KiB"):
        store.save(payload)


def test_persistent_store_durable_write_probe_leaves_no_file(tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)

    store.ensure_durable_writable()

    assert not list((tmp_path / "secrets").glob(".credential-write-probe*"))


def test_environment_store_rejects_durable_write_probe() -> None:
    with pytest.raises(RuntimeError, match="不能耐久保存"):
        EnvCredentialStore().ensure_durable_writable()


def test_environment_store_rejects_invalid_gateway_port(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_CREDENTIAL_WORKER_ID", "worker-001")
    monkeypatch.setenv("WORKER_CREDENTIAL_API_KEY", "api-key")
    monkeypatch.setenv("WORKER_CREDENTIAL_SECRET_KEY", "secret-key")
    monkeypatch.setenv("WORKER_CREDENTIAL_GATEWAY_PORT", "invalid")

    with pytest.raises(ValueError, match="invalid literal"):
        EnvCredentialStore().load()


def test_environment_store_is_read_only() -> None:
    with pytest.raises(RuntimeError, match="只读"):
        EnvCredentialStore().save(_credentials().to_dict())


def test_registration_intent_is_private_stable_and_durable(tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    request = _registration_request()

    with store.registration_session("install-key", request) as first:
        assert first is not None
        intent_path = tmp_path / "secrets" / "worker_registration_intent.json"
        assert stat.S_IMODE(intent_path.stat().st_mode) == 0o600
    with store.registration_session("install-key", request) as recovered:
        assert recovered == first
        store.finish_registration()

    assert not intent_path.exists()


def test_registration_intent_rejects_different_install_key(tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    with store.registration_session("first-key", _registration_request()):
        pass

    with pytest.raises(RuntimeError, match="安装 Key 不一致"):
        with store.registration_session("second-key", _registration_request()):
            pass


def test_saved_worker_config_is_private_and_excludes_install_key(tmp_path: Path) -> None:
    config_path = tmp_path / "worker_config.yaml"
    config = WorkerConfig(redis_url="redis://user:password@redis.example.com/0")

    config.save_to_file(config_path)

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    content = config_path.read_text(encoding="utf-8")
    assert "redis://user:password@redis.example.com/0" in content
    assert "ANTCODE_WORKER_KEY" not in content


def test_install_key_registration_fails_when_credentials_cannot_persist(monkeypatch, tmp_path: Path) -> None:
    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url, *, json, headers=None):
            assert headers is None
            assert json["key"] == "install-key"
            assert json["transport_mode"] == "direct"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "worker_id": "worker-001",
                        "api_key": "api-key",
                        "secret_key": "secret-key",
                        "registration_id": json["registration_id"],
                        "protocol_version": 2,
                        "recovered": False,
                        "recovery_expires_at": "2026-07-17T12:00:00+00:00",
                    },
                },
            )

    store = PersistentCredentialStore(tmp_path)
    credential_service = CredentialService(store)

    def reject_save(_credentials):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", reject_save)
    config = SimpleNamespace(
        worker_key="install-key",
        gateway_host="gateway.example.com",
        gateway_port=50051,
        api_base_url="https://control.example.com",
        host="10.0.0.2",
        name="Worker-001",
        port=8001,
        region="",
        transport_mode="direct",
    )
    monkeypatch.setattr(httpx, "Client", Client)

    with pytest.raises(OSError, match="disk full"):
        wiring._register_by_install_key(config, credential_service)
    assert (tmp_path / "secrets" / "worker_registration_intent.json").exists()


def test_install_key_registration_persists_then_acknowledges(monkeypatch, tmp_path: Path) -> None:
    requests: list[tuple[str, dict, dict | None]] = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url, *, json, headers=None):
            requests.append((url, json, headers))
            if url.endswith("register-by-key-v2"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "worker_id": "worker-001",
                            "api_key": "api-key",
                            "secret_key": "secret-key",
                            "registration_id": json["registration_id"],
                            "protocol_version": 2,
                            "recovered": False,
                            "recovery_expires_at": "2026-07-17T12:00:00+00:00",
                        },
                    },
                )
            return httpx.Response(200, json={"success": True, "data": {}})

    config = SimpleNamespace(
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
    service = CredentialService(PersistentCredentialStore(tmp_path))
    monkeypatch.setattr(httpx, "Client", Client)

    credentials = wiring._register_by_install_key(config, service)

    assert credentials.worker_id == "worker-001"
    assert credentials.registration_id
    assert len(requests) == 2
    assert requests[1][0].endswith("/workers/worker-001/registration-ack")
    assert requests[1][2]["X-Signature"]
    assert not (tmp_path / "secrets" / "worker_registration_intent.json").exists()


def test_restart_with_saved_credentials_only_retries_registration_ack(monkeypatch, tmp_path: Path) -> None:
    store = PersistentCredentialStore(tmp_path)
    request = _registration_request()
    with store.registration_session("install-key", request) as intent:
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

        def post(self, url, *, json, headers=None):
            calls.append(url)
            assert json == {"registration_id": credentials.registration_id}
            assert headers and headers["X-Signature"]
            return httpx.Response(200, json={"success": True, "data": {}})

    monkeypatch.setattr(httpx, "Client", Client)

    resume_registration_ack(CredentialService(store), credentials)

    assert len(calls) == 1
    assert calls[0].endswith("/workers/worker-001/registration-ack")
    assert not (tmp_path / "secrets" / "worker_registration_intent.json").exists()


def test_install_key_registration_does_not_send_when_store_is_not_durable(monkeypatch) -> None:
    def reject_client(**_kwargs):
        raise AssertionError("持久化预检失败后不得创建 HTTP 客户端")

    def reject_storage() -> None:
        raise OSError("read-only filesystem")

    credential_service = SimpleNamespace(ensure_durable_writable=reject_storage)
    config = SimpleNamespace(worker_key="install-key")
    monkeypatch.setattr(httpx, "Client", reject_client)

    with pytest.raises(OSError, match="read-only filesystem"):
        wiring._register_by_install_key(config, credential_service)


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

        def post(self, url, *, json, headers):
            captured.update(url=url, json=json, headers=headers)
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
    assert captured["headers"]["X-Signature"]
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
