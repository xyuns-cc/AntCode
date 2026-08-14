from importlib.util import find_spec

from antcode_web_api.routes import api_router
from antcode_web_api.routes.v1 import v1_router
from fastapi import FastAPI


def _module_is_absent(name: str) -> bool:
    try:
        return find_spec(name) is None
    except ModuleNotFoundError:
        return True


def test_api_router_has_routes():
    assert len(api_router.routes) > 0


def test_v1_router_has_core_prefixes():
    app = FastAPI()
    app.include_router(v1_router)
    paths = set(app.openapi()["paths"])
    assert any(path.startswith("/tasks") for path in paths)
    assert any(path.startswith("/runs") for path in paths)
    assert any(path.startswith("/runtimes") for path in paths)


def test_realtime_log_transport_only_registers_sse():
    app = FastAPI()
    app.include_router(v1_router)
    paths = set(app.openapi()["paths"])

    assert "/logs/runs/{run_id}/stream" in paths
    assert _module_is_absent("antcode_web_api.routes.v1.websocket_logs")
    assert _module_is_absent("antcode_web_api.websockets.websocket_connection_manager")
