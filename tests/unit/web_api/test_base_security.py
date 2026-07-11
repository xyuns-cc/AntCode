from types import SimpleNamespace

import pytest
from antcode_web_api.routes.v1 import base as base_route
from fastapi import HTTPException, Response


def test_forgot_password_route_removed():
    paths = [getattr(route, "path", "") for route in base_route.router.routes]
    assert "/auth/forgot-password" not in paths


def test_reset_password_route_removed():
    paths = [getattr(route, "path", "") for route in base_route.router.routes]
    assert "/auth/reset-password" not in paths


def test_refresh_cookie_is_http_only_and_same_site(monkeypatch):
    monkeypatch.setattr(base_route.settings, "AUTH_COOKIE_SECURE", True)
    response = Response()

    base_route._set_refresh_cookie(response, "refresh-token")

    header = response.headers["set-cookie"].lower()
    assert "antcode_refresh=refresh-token" in header
    assert "httponly" in header
    assert "samesite=strict" in header
    assert "secure" in header
    assert "path=/api/v1/auth" in header


def test_refresh_token_prefers_http_only_cookie():
    body = base_route.RefreshTokenRequest(refresh_token="body-token")
    request = SimpleNamespace(cookies={"antcode_refresh": "cookie-token"})

    assert base_route._resolve_refresh_token(body, request) == "cookie-token"


def test_refresh_token_requires_cookie_or_explicit_api_token():
    request = SimpleNamespace(cookies={})

    with pytest.raises(HTTPException) as exc_info:
        base_route._resolve_refresh_token(None, request)

    assert exc_info.value.status_code == 401
