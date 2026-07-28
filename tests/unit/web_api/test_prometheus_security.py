from antcode_core.common.security.auth import get_current_admin_user
from antcode_web_api.prometheus_metrics import router


def test_metrics_route_requires_admin_authentication() -> None:
    route = next(route for route in router.routes if route.path == "/metrics")
    dependencies = [dependency.call for dependency in route.dependant.dependencies]

    assert get_current_admin_user in dependencies
