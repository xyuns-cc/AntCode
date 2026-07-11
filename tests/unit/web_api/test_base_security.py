from antcode_web_api.routes.v1 import base as base_route


def test_forgot_password_route_removed():
    paths = [getattr(route, "path", "") for route in base_route.router.routes]
    assert "/auth/forgot-password" not in paths


def test_reset_password_route_removed():
    paths = [getattr(route, "path", "") for route in base_route.router.routes]
    assert "/auth/reset-password" not in paths
