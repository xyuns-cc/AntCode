"""Master 的存活探针与就绪探针必须分开。

用例跑的是真的 asyncio server 和真的 TCP 请求，并通过公开的 ``add_probe``
注入一个恒失败的组件探针（scheduler 激活失败走的就是这条路），于是：

  * ``/health/ready`` 必须 503：这个实例确实接不了活，要挡住流量与部署放行；
  * ``/health/live``  必须 200：激活失败下一轮 leader poll 会自己重试，Redis /
    Postgres 不可达重启 Master 也修不好——用就绪结论决定重启，一次中间件抖动
    就能把 web-api / master / gateway 一起打进重启循环。

改之前 ``/health/live`` 走不到任何分支，恒定 503。
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from antcode_master.readiness import MasterReadinessServer

pytestmark = pytest.mark.asyncio

RESPONSE_LIMIT_BYTES = 4096
OK_STATUS = b"200 OK"
UNAVAILABLE_STATUS = b"503 Service Unavailable"


async def _request(port: int, path: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: probe\r\n\r\n".encode())
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(RESPONSE_LIMIT_BYTES), timeout=10)
    finally:
        writer.close()


@pytest_asyncio.fixture
async def probe_port(monkeypatch) -> int:
    monkeypatch.setenv("MASTER_READINESS_PORT", "0")
    server = MasterReadinessServer()
    server.add_probe(lambda: False)
    await server.start()
    try:
        yield server._server.sockets[0].getsockname()[1]
    finally:
        await server.stop()


async def test_liveness_is_served_while_a_component_probe_is_failing(probe_port: int) -> None:
    response = await _request(probe_port, "/health/live")

    assert OK_STATUS in response, response


async def test_readiness_fails_when_a_component_probe_fails(probe_port: int) -> None:
    response = await _request(probe_port, "/health/ready")

    assert UNAVAILABLE_STATUS in response, response


async def test_unknown_paths_stay_unavailable(probe_port: int) -> None:
    """探针服务只认这两条路径，别的一律 503——不能让打错的路径变成"恒健康"。"""
    response = await _request(probe_port, "/healthz")

    assert UNAVAILABLE_STATUS in response, response


async def test_liveness_fails_once_the_probe_server_is_stopped(monkeypatch) -> None:
    """停机途中不再自称存活：fail-closed 的另一半。"""
    monkeypatch.setenv("MASTER_READINESS_PORT", "0")
    server = MasterReadinessServer()
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    server._ready = False

    response = await _request(port, "/health/live")
    await server.stop()

    assert UNAVAILABLE_STATUS in response, response
