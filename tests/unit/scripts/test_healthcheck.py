import json
from pathlib import Path

import pytest

from scripts import healthcheck


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = json.dumps(payload).encode("utf-8")
        self._status_code = status_code

    def getcode(self) -> int:
        return self._status_code

    def read(self) -> bytes:
        return self._payload


class _Connection:
    def __init__(self, payload: dict) -> None:
        self._response = _Response(payload)
        self.request_args = None
        self.closed = False

    def request(self, *args) -> None:
        self.request_args = args

    def getresponse(self) -> _Response:
        return self._response

    def close(self) -> None:
        self.closed = True


def test_healthcheck_uses_public_endpoint_without_credentials(monkeypatch) -> None:
    connection = _Connection({"success": True, "data": {"status": "healthy"}})
    monkeypatch.setattr(healthcheck, "_connection", lambda url: (connection, healthcheck._parse_http_url(url).path))
    monkeypatch.setenv("HEALTHCHECK_BASE_URL", "http://127.0.0.1:8000/")

    assert healthcheck.main() == 0
    assert connection.request_args == ("GET", "/api/v1/health")
    assert connection.closed is True


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "data": {"status": "healthy"}},
        {"success": True, "data": {"status": "unhealthy"}},
        {"success": True, "data": None},
    ],
)
def test_healthcheck_rejects_non_healthy_payload(monkeypatch, payload, capsys) -> None:
    connection = _Connection(payload)
    monkeypatch.setattr(healthcheck, "_connection", lambda _url: (connection, "/api/v1/health"))

    assert healthcheck.main() == 1
    assert "response is not healthy" in capsys.readouterr().err
    assert connection.closed is True


def test_healthcheck_rejects_non_http_base_url(monkeypatch, capsys) -> None:
    monkeypatch.setenv("HEALTHCHECK_BASE_URL", "file:///etc/passwd")

    assert healthcheck.main() == 1
    assert "HTTP(S)" in capsys.readouterr().err


def test_healthcheck_source_contains_no_credentials_or_token_cache() -> None:
    source = Path(healthcheck.__file__).read_text(encoding="utf-8")

    for forbidden in ("Admin123!", "HEALTHCHECK_PASSWORD", "DEFAULT_ADMIN_PASSWORD", "TOKEN_CACHE", "/auth/login"):
        assert forbidden not in source
