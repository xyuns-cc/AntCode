"""静态路由不得被更早注册的参数路由遮蔽。

历史事故：``GET /api/v1/workers/install-keys`` 注册在 ``GET /{worker_id}`` 之后,
被后者吃掉,管理员拉安装 Key 列表恒返回 404「Worker 不存在」。修复方式是让
``promote_static_routes`` 无条件前置所有静态路由;这里把不变量固化为断言,
避免新增静态路由时重新引入遮蔽。
"""

from __future__ import annotations

import pytest
from antcode_web_api.routes.v1.users import router as users_router
from antcode_web_api.routes.v1.workers import workers_router
from antcode_web_api.routing import find_shadowed_routes, promote_static_routes
from fastapi import APIRouter

ROUTERS = [
    pytest.param(workers_router, id="workers"),
    pytest.param(users_router, id="users"),
]


@pytest.mark.parametrize("router", ROUTERS)
def test_no_static_route_is_shadowed(router: APIRouter) -> None:
    shadowed = find_shadowed_routes(router)
    assert shadowed == [], f"静态路由被参数路由遮蔽: {shadowed}"


def test_install_keys_precedes_worker_id_route() -> None:
    order = _first_index_by_method_path(workers_router)
    assert order[("GET", "/install-keys")] < order[("GET", "/{worker_id}")]


def test_promote_static_routes_orders_all_static_ahead_of_parameterised() -> None:
    router = APIRouter()

    @router.get("/{item_id}")
    async def _by_id(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    @router.get("/summary")
    async def _summary() -> dict[str, bool]:
        return {"ok": True}

    assert find_shadowed_routes(router) != []

    promote_static_routes(router)

    assert find_shadowed_routes(router) == []
    order = _first_index_by_method_path(router)
    assert order[("GET", "/summary")] < order[("GET", "/{item_id}")]


def _first_index_by_method_path(router: APIRouter) -> dict[tuple[str, str], int]:
    order: dict[tuple[str, str], int] = {}
    for route in router.routes:
        path = getattr(route, "path", "") or ""
        for method in sorted(getattr(route, "methods", None) or set()):
            if method in ("HEAD", "OPTIONS"):
                continue
            order.setdefault((method, path), len(order))
    return order
