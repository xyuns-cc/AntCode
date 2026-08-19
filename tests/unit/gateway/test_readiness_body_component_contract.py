"""Gateway 的探针响应体必须把 gRPC 组件单独列出来。

Gateway 没有独立的 live 端点，compose 的存活探针因此读的是
``/health/ready`` 响应体里的 `"grpc":"ok"` 片段而不是聚合状态码
（docker-compose.prod.control.yml）。这条契约一旦被改掉——比如响应体换格式、
或者 grpc 字段跟着 Redis/DB 一起翻——存活探针会在中间件抖动时把 Gateway 杀掉，
所有 Worker 跟着掉线。所以这里把它钉死。

依赖检查在本用例里被替换成"确定不可达"，为的是稳定构造"依赖全挂但 gRPC 还在
listen"这个场景；被断言的响应体拼装与状态码判定跑的都是真实实现。
"""

from __future__ import annotations

import pytest
from antcode_gateway.server import GrpcServer

LIVENESS_FRAGMENT = '"grpc":"ok"'
DOWN_FRAGMENT = '"grpc":"down"'
UNAVAILABLE = 503

pytestmark = pytest.mark.asyncio


def _server_with_unreachable_dependencies(*, listening: bool) -> GrpcServer:
    server = GrpcServer()
    server._started = listening
    server._server = object() if listening else None  # type: ignore[assignment]

    async def _redis_down() -> tuple[bool, str]:
        return False, "redis_error:ConnectionError"

    async def _db_down() -> tuple[bool, str]:
        return False, "db_not_inited"

    server._check_redis_ready = _redis_down  # type: ignore[method-assign]
    server._check_db_ready = _db_down  # type: ignore[method-assign]
    return server


async def test_grpc_component_stays_ok_while_dependencies_are_down() -> None:
    """依赖全挂 → 整体 503（挡流量），但 gRPC 组件仍是 ok（不该重启）。"""
    status, body = await _server_with_unreachable_dependencies(listening=True)._readiness_response()

    assert status == UNAVAILABLE
    assert LIVENESS_FRAGMENT in body, body


async def test_grpc_component_reports_down_when_the_server_is_not_listening() -> None:
    """gRPC 没在 listen 才是"重启才能修"的故障，存活探针必须能看见。"""
    status, body = await _server_with_unreachable_dependencies(listening=False)._readiness_response()

    assert status == UNAVAILABLE
    assert DOWN_FRAGMENT in body, body
    assert LIVENESS_FRAGMENT not in body, body
