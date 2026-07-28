import pytest

from tests.e2e.conftest import (
    E2E_CONFIRM_ENV,
    E2E_DATABASE_HOST_ENV,
    E2E_DATABASE_NAME,
    E2E_DATABASE_NAME_ENV,
    E2E_TRANSPORT_MODE_ENV,
    E2E_WORKER_ID_ENV,
    require_e2e_authorization,
    require_e2e_transport_mode,
    require_e2e_worker_id,
)
from tests.e2e.helpers import assert_worker_transport_mode


def _configure_full_e2e(monkeypatch, database_url: str) -> None:
    monkeypatch.setenv(E2E_CONFIRM_ENV, "FULL")
    monkeypatch.setenv(E2E_WORKER_ID_ENV, "worker-e2e-001")
    monkeypatch.setenv(E2E_DATABASE_HOST_ENV, "db")
    monkeypatch.setenv(E2E_DATABASE_NAME_ENV, E2E_DATABASE_NAME)
    monkeypatch.setenv("DATABASE_URL", database_url)


def test_e2e_requires_explicit_full_confirmation(monkeypatch) -> None:
    monkeypatch.delenv(E2E_CONFIRM_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://user:pass@db/{E2E_DATABASE_NAME}")

    with pytest.raises(RuntimeError, match=E2E_CONFIRM_ENV):
        require_e2e_authorization()


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:pass@db/AntCode",
        "postgresql://user:pass@db/antcode_migration_test",
        "sqlite:///antcode_e2e_test",
    ],
)
def test_e2e_rejects_non_dedicated_database(monkeypatch, database_url: str) -> None:
    _configure_full_e2e(monkeypatch, database_url)

    with pytest.raises(ValueError):
        require_e2e_authorization()


def test_e2e_accepts_only_dedicated_database(monkeypatch) -> None:
    database_url = f"postgresql://user:pass@db/{E2E_DATABASE_NAME}"
    _configure_full_e2e(monkeypatch, database_url)

    assert require_e2e_authorization() == database_url


@pytest.mark.parametrize("missing_env", [E2E_DATABASE_HOST_ENV, E2E_DATABASE_NAME_ENV])
def test_e2e_requires_explicit_database_binding(monkeypatch, missing_env: str) -> None:
    database_url = f"postgresql://user:pass@db/{E2E_DATABASE_NAME}"
    _configure_full_e2e(monkeypatch, database_url)
    monkeypatch.delenv(missing_env)

    with pytest.raises(RuntimeError, match=missing_env):
        require_e2e_authorization()


def test_e2e_rejects_database_host_binding_mismatch(monkeypatch) -> None:
    database_url = f"postgresql://user:pass@other-db/{E2E_DATABASE_NAME}"
    _configure_full_e2e(monkeypatch, database_url)

    with pytest.raises(ValueError, match="host 绑定不匹配"):
        require_e2e_authorization()


def test_e2e_rejects_non_dedicated_database_binding(monkeypatch) -> None:
    database_url = f"postgresql://user:pass@db/{E2E_DATABASE_NAME}"
    _configure_full_e2e(monkeypatch, database_url)
    monkeypatch.setenv(E2E_DATABASE_NAME_ENV, "production")

    with pytest.raises(ValueError, match=E2E_DATABASE_NAME_ENV):
        require_e2e_authorization()


@pytest.mark.parametrize("worker_id", [None, "", "   "])
def test_full_e2e_requires_dedicated_worker_id(monkeypatch, worker_id: str | None) -> None:
    database_url = f"postgresql://user:pass@db/{E2E_DATABASE_NAME}"
    _configure_full_e2e(monkeypatch, database_url)
    if worker_id is None:
        monkeypatch.delenv(E2E_WORKER_ID_ENV, raising=False)
    else:
        monkeypatch.setenv(E2E_WORKER_ID_ENV, worker_id)

    with pytest.raises(RuntimeError, match=E2E_WORKER_ID_ENV):
        require_e2e_authorization()


def test_full_e2e_accepts_explicit_dedicated_worker_id(monkeypatch) -> None:
    monkeypatch.setenv(E2E_WORKER_ID_ENV, " worker-e2e-001 ")

    assert require_e2e_worker_id() == "worker-e2e-001"


def test_e2e_requires_explicit_transport_mode(monkeypatch) -> None:
    monkeypatch.delenv(E2E_TRANSPORT_MODE_ENV, raising=False)

    with pytest.raises(RuntimeError, match=E2E_TRANSPORT_MODE_ENV):
        require_e2e_transport_mode()


@pytest.mark.parametrize("mode", ["direct", "gateway"])
def test_e2e_accepts_supported_transport_modes(monkeypatch, mode: str) -> None:
    monkeypatch.setenv(E2E_TRANSPORT_MODE_ENV, mode.upper())

    assert require_e2e_transport_mode() == mode


def test_e2e_rejects_unknown_transport_mode(monkeypatch) -> None:
    monkeypatch.setenv(E2E_TRANSPORT_MODE_ENV, "redis")

    with pytest.raises(RuntimeError, match=E2E_TRANSPORT_MODE_ENV):
        require_e2e_transport_mode()


def test_worker_transport_mode_assertion_accepts_exact_mode() -> None:
    assert_worker_transport_mode({"id": "worker-1", "transportMode": "direct"}, "direct")


def test_worker_transport_mode_assertion_rejects_wrong_mode() -> None:
    worker = {"id": "worker-1", "transportMode": "gateway"}

    with pytest.raises(AssertionError, match="expected='direct'.*actual='gateway'"):
        assert_worker_transport_mode(worker, "direct")


def test_worker_transport_mode_assertion_rejects_missing_mode() -> None:
    with pytest.raises(AssertionError, match="缺少 transportMode"):
        assert_worker_transport_mode({"id": "worker-1"}, "gateway")


@pytest.mark.parametrize("transport_mode", [None, "", "   "])
def test_worker_transport_mode_assertion_rejects_empty_mode(transport_mode) -> None:
    worker = {"id": "worker-1", "transportMode": transport_mode}

    with pytest.raises(AssertionError, match="transportMode 为空"):
        assert_worker_transport_mode(worker, "gateway")
