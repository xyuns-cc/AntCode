"""
Gateway 服务入口

启动 gRPC 网关服务。
"""

import asyncio
import os
import signal
import sys

from antcode_contracts.control_pb2_grpc import add_ControlServiceServicer_to_server
from antcode_contracts.data_pb2_grpc import add_DataServiceServicer_to_server
from antcode_core.application.services.lease_service import LeasePolicy, LeaseStore
from antcode_core.infrastructure.db.tortoise import close_db, init_db
from antcode_core.infrastructure.redis import get_redis_client, redis_namespace
from antcode_core.infrastructure.redis.stream_client import StreamClient
from loguru import logger

from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.config import gateway_config
from antcode_gateway.rate_limit import RateLimitInterceptor
from antcode_gateway.server import get_grpc_server
from antcode_gateway.services import GatewayControlService, GatewayDataService


def _configure_interceptors(server) -> None:
    if gateway_config.auth_enabled:
        audit_stream = StreamClient()
        server.add_interceptor(AuthInterceptor(enabled=True, audit_stream=audit_stream))
        logger.info("AuthInterceptor 已启用（含安全审计 Stream）")
    else:
        server.add_interceptor(AuthInterceptor(enabled=False))
        logger.info("AuthInterceptor 已禁用")

    if gateway_config.rate_limit_enabled:
        server.add_interceptor(
            RateLimitInterceptor(
                enabled=True,
                rate=gateway_config.rate_limit_rate,
                capacity=gateway_config.rate_limit_capacity,
            )
        )
        logger.info("RateLimitInterceptor 已启用")
    else:
        logger.info("RateLimitInterceptor 已禁用")


async def _create_lease_store() -> LeaseStore:
    redis_client = await get_redis_client()
    if redis_client is None:
        raise RuntimeError("Redis client unavailable")
    lease_store = LeaseStore(
        redis_client=redis_client,
        namespace=redis_namespace(),
        policy=LeasePolicy(),
    )
    logger.info(
        "LeaseStore 已构建: namespace={} ttl_ms={} renew_after_ms={}",
        lease_store.namespace,
        lease_store.policy.ttl_ms,
        lease_store.policy.renew_after_ms,
    )
    return lease_store


def _register_services(server, lease_store: LeaseStore) -> None:
    logger.info("注册 gRPC 服务")
    server.add_servicer(
        GatewayControlService(lease_store=lease_store),
        add_ControlServiceServicer_to_server,
    )
    server.add_servicer(GatewayDataService(), add_DataServiceServicer_to_server)
    logger.info("ControlService + DataService 已注册")


async def _close_db_with_timeout(timeout: float, message: str) -> None:
    try:
        await asyncio.wait_for(close_db(), timeout=timeout)
    except TimeoutError:
        logger.warning(message)
    except Exception:
        logger.exception(message)


class _ShutdownCoordinator:
    def __init__(self, server):
        self.server = server
        self.event = asyncio.Event()
        self.started = False
        self.loop = asyncio.get_running_loop()

    async def shutdown(self, sig: int | None = None) -> None:
        if self.started:
            return
        self.started = True
        if sig is not None:
            sig_name = signal.Signals(sig).name if isinstance(sig, int) else str(sig)
            logger.info(f"收到信号 {sig_name}，准备关闭...")
        else:
            logger.info("触发关闭流程，准备关闭...")

        logger.info("正在关闭 Gateway 服务...")
        try:
            await asyncio.wait_for(
                self.server.stop(),
                timeout=gateway_config.shutdown_grace_period + 5,
            )
        except TimeoutError:
            logger.warning("gRPC 服务器关闭超时，继续关闭流程")
        await _close_db_with_timeout(10, "数据库关闭超时或失败，继续退出")
        self.event.set()
        logger.info("Gateway 服务已关闭")

    def request(self, signum: int) -> None:
        if self.started:
            logger.warning("收到重复退出信号，强制退出")
            os._exit(128 + signum)
        self.loop.create_task(self.shutdown(signum))

    def _sync_signal_handler(self, signum, _frame) -> None:
        self.loop.call_soon_threadsafe(self.request, signum)

    def install_signal_handlers(self) -> None:
        if sys.platform == "win32":
            signal.signal(signal.SIGINT, self._sync_signal_handler)
            signal.signal(signal.SIGTERM, self._sync_signal_handler)
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, self.request, sig)

    async def wait(self) -> None:
        server_task = asyncio.create_task(self.server.wait_for_termination())
        shutdown_task = asyncio.create_task(self.event.wait())
        done, pending = await asyncio.wait(
            {server_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if server_task in done and not self.started:
            await self.shutdown(None)


async def main():
    """主函数"""
    logger.info("Gateway 服务启动")
    await init_db(service="gateway")
    server = get_grpc_server()
    _configure_interceptors(server)
    try:
        lease_store = await _create_lease_store()
    except Exception as exc:
        logger.exception(f"构建 LeaseStore 失败，Gateway 拒绝启动: {exc}")
        await close_db()
        raise
    _register_services(server, lease_store)
    if not await server.start():
        logger.error("Gateway gRPC 服务器启动失败，触发退出")
        await _close_db_with_timeout(5, "退出前 close_db 失败,忽略")
        sys.exit(1)
    logger.info("Gateway 服务已启动")
    coordinator = _ShutdownCoordinator(server)
    coordinator.install_signal_handlers()
    try:
        await coordinator.wait()
    finally:
        await coordinator.shutdown(None)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("服务已停止")
        sys.exit(0)
