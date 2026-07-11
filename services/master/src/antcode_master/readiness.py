"""Master HTTP readiness endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable
from typing import cast

from antcode_core.infrastructure.redis import get_redis_client
from loguru import logger
from tortoise import Tortoise

READ_TIMEOUT_SECONDS = 2.0


class MasterReadinessServer:
    """Reports event-loop and dependency readiness to the container runtime."""

    def __init__(self) -> None:
        self._host = os.getenv("MASTER_READINESS_HOST", "0.0.0.0")
        self._port = int(os.getenv("MASTER_READINESS_PORT", "8101"))
        self._server: asyncio.Server | None = None
        self._ready = False

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_request,
            self._host,
            self._port,
        )
        self._ready = True
        logger.info(f"Master readiness 已监听 {self._host}:{self._port}")

    async def stop(self) -> None:
        self._ready = False
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await asyncio.wait_for(
                reader.readline(),
                timeout=READ_TIMEOUT_SECONDS,
            )
            path = self._request_path(request)
            ready = path == "/health/ready" and await self._dependencies_ready()
            writer.write(self._response(ready))
            await writer.drain()
        except Exception as exc:
            logger.warning(f"Master readiness 请求失败: {exc}")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dependencies_ready(self) -> bool:
        if not self._ready:
            return False
        try:
            redis = await get_redis_client()
            await cast(Awaitable[bool], redis.ping())
            db = Tortoise.get_connection("default")
            await db.execute_query("SELECT 1")
            return True
        except Exception as exc:
            logger.warning(f"Master readiness 依赖检查失败: {exc}")
            return False

    @staticmethod
    def _request_path(request: bytes) -> str:
        parts = request.decode("ascii", errors="replace").split()
        return parts[1] if len(parts) >= 2 else ""

    @staticmethod
    def _response(ready: bool) -> bytes:
        status = b"200 OK" if ready else b"503 Service Unavailable"
        body = b"ready\n" if ready else b"not ready\n"
        return b"HTTP/1.1 " + status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


master_readiness = MasterReadinessServer()
