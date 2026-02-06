from antcode_web_api.routes import api_router
from antcode_web_api.routes.v1 import v1_router


def test_api_router_has_routes():
    assert len(api_router.routes) > 0


def test_v1_router_has_core_prefixes():
    paths = {route.path for route in v1_router.routes if hasattr(route, "path")}
    assert any(path.startswith("/tasks") for path in paths)
    assert any(path.startswith("/runs") for path in paths)
    assert any(path.startswith("/runtimes") for path in paths)
