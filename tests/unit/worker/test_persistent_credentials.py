import os
import stat
from pathlib import Path

import pytest
from antcode_worker.app.worker_registration import (
    _should_trust_env_proxy,
    normalize_api_base_url,
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
        "http://192.168.1.10:8000",
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


def test_describe_location_points_at_the_real_credential_file(monkeypatch, tmp_path: Path) -> None:
    """报错里的"请清除 <路径>"必须指向真实存在的那一份，且带上环境变量兜底。

    单测里给 store 打桩很容易让"路径正确"这件事在假对象上通过；这里绑真实
    ``PersistentCredentialStore``，一正一反：文件路径确实是被写入的那个，
    环境变量来源也被提到（只删文件而 WORKER_CREDENTIAL_* 还在，重启会把同一份
    失效身份读回来）。
    """
    _clear_credential_env(monkeypatch)
    store = PersistentCredentialStore(tmp_path)
    assert CredentialService(store).save(_credentials("file-worker"))
    written = tmp_path / "secrets" / "worker_credentials.json"
    assert written.exists()

    location = store.describe_location()

    assert str(written) in location
    assert "WORKER_CREDENTIAL_" in location
