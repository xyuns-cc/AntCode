from pathlib import Path

import pytest

from tests.e2e.conftest import (
    E2E_CONFIRM_ENV,
    E2E_TRANSPORT_MODE_ENV,
    E2E_WEB_API_URL_ENV,
    E2E_WORKER_ID_ENV,
    require_e2e_authorization,
    require_e2e_transport_mode,
    require_e2e_worker_id,
)
from tests.e2e.helpers import assert_worker_transport_mode


def _configure_full_e2e(monkeypatch, web_api_url: str = "https://antcode.example.com") -> None:
    monkeypatch.setenv(E2E_CONFIRM_ENV, "FULL")
    monkeypatch.setenv(E2E_WORKER_ID_ENV, "worker-e2e-001")
    monkeypatch.setenv(E2E_WEB_API_URL_ENV, web_api_url)


def test_e2e_requires_explicit_full_confirmation(monkeypatch) -> None:
    monkeypatch.delenv(E2E_CONFIRM_ENV, raising=False)
    monkeypatch.setenv(E2E_WEB_API_URL_ENV, "https://antcode.example.com")

    with pytest.raises(RuntimeError, match=E2E_CONFIRM_ENV):
        require_e2e_authorization()


@pytest.mark.parametrize(
    "web_api_url",
    [
        "http://antcode.example.com",
        "https://user:pass@antcode.example.com",
        "https://antcode.example.com/control",
        "https://antcode.example.com?debug=true",
    ],
)
def test_e2e_rejects_non_https_origin(monkeypatch, web_api_url: str) -> None:
    _configure_full_e2e(monkeypatch, web_api_url)

    with pytest.raises(ValueError):
        require_e2e_authorization()


def test_e2e_accepts_explicit_https_origin(monkeypatch) -> None:
    _configure_full_e2e(monkeypatch, "https://antcode.example.com/")

    assert require_e2e_authorization() == "https://antcode.example.com"


def test_e2e_accepts_explicit_loopback_http_for_local_ci(monkeypatch) -> None:
    _configure_full_e2e(monkeypatch, "http://127.0.0.1:8000")

    assert require_e2e_authorization() == "http://127.0.0.1:8000"


def test_e2e_requires_explicit_web_api_url(monkeypatch) -> None:
    _configure_full_e2e(monkeypatch)
    monkeypatch.delenv(E2E_WEB_API_URL_ENV)

    with pytest.raises(RuntimeError, match=E2E_WEB_API_URL_ENV):
        require_e2e_authorization()


def test_e2e_fixtures_never_mutate_database() -> None:
    fixture_source = Path("tests/e2e/conftest.py").read_text(encoding="utf-8")

    for forbidden in ("Tortoise.init", "User.filter", ".set_password(", ".save("):
        assert forbidden not in fixture_source


def test_e2e_spider_cleanup_uses_explicit_redis_without_database_settings() -> None:
    cleanup_source = Path("tests/e2e/spider_data_scenario.py").read_text(encoding="utf-8")

    assert "ANTCODE_E2E_REDIS_URL" in cleanup_source
    assert "ANTCODE_E2E_REDIS_NAMESPACE" in cleanup_source
    assert "antcode_core.common.config" not in cleanup_source
    assert "DATABASE_URL" not in cleanup_source


@pytest.mark.parametrize("worker_id", [None, "", "   "])
def test_full_e2e_requires_dedicated_worker_id(monkeypatch, worker_id: str | None) -> None:
    _configure_full_e2e(monkeypatch)
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
