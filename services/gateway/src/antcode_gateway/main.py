"""
Gateway 服务入口

启动 gRPC 网关服务。
"""

import asyncio
import os
import signal
import sys

from loguru import logger

from antcode_contracts.gateway_pb2_grpc import add_GatewayServiceServicer_to_server
from antcode_core.infrastructure.db.tortoise import close_db, init_db
from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.config import gateway_config
from antcode_gateway.rate_limit import RateLimitInterceptor
from antcode_gateway.server import get_grpc_server
from antcode_gateway.services import GatewayServiceImpl


async def main():
    """主函数"""
    logger.info("Gateway 服务启动")

    await init_db()

    # 获取服务器实例
    server = get_grpc_server()

    # 注册拦截器
    if gateway_config.auth_enabled:
        server.add_interceptor(AuthInterceptor(enabled=True))
        logger.info("AuthInterceptor 已启用")
    else:
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

    # 注册服务实现
    logger.info("注册 gRPC 服务")
    server.add_servicer(GatewayServiceImpl(), add_GatewayServiceServicer_to_server)
    logger.info("GatewayService 已注册")

    # 启动服务器
    await server.start()
    logger.info("Gateway 服务已启动")

    shutdown_event = asyncio.Event()
    shutdown_started = False
    loop = asyncio.get_running_loop()

    async def shutdown(sig: int | None = None) -> None:
        """关闭服务"""
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True

        if sig is not None:
            sig_name = signal.Signals(sig).name if isinstance(sig, int) else str(sig)
            logger.info(f"收到信号 {sig_name}，准备关闭...")
        else:
            logger.info("触发关闭流程，准备关闭...")

        logger.info("正在关闭 Gateway 服务...")
        try:
            await asyncio.wait_for(
                server.stop(),
                timeout=gateway_config.shutdown_grace_period + 5,
            )
        except TimeoutError:
            logger.warning("gRPC 服务器关闭超时，继续关闭流程")

        try:
            await asyncio.wait_for(close_db(), timeout=10)
        except TimeoutError:
            logger.warning("数据库关闭超时，继续退出")

        shutdown_event.set()
        logger.info("Gateway 服务已关闭")

    def request_shutdown(signum: int) -> None:
        if shutdown_started:
            logger.warning("收到重复退出信号，强制退出")
            os._exit(128 + signum)
        loop.create_task(shutdown(signum))

    if sys.platform == "win32":
        def _sync_signal_handler(signum, _frame):
            loop.call_soon_threadsafe(request_shutdown, signum)
        signal.signal(signal.SIGINT, _sync_signal_handler)
        signal.signal(signal.SIGTERM, _sync_signal_handler)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: request_shutdown(s))

    # 等待服务器终止或收到关闭信号
    try:
        server_task = asyncio.create_task(server.wait_for_termination())
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait(
            {server_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if server_task in done and not shutdown_started:
            await shutdown(None)
    finally:
        await shutdown(None)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("服务已停止")
        sys.exit(0)
