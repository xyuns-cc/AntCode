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


async def main():
    """主函数"""
    logger.info("Gateway 服务启动")

    # P2-a2: 必须传 service="gateway",否则 helper 走 default 池,
    # DB_POOL_MAX_GATEWAY 配置将静默失效。
    await init_db(service="gateway")

    # 获取服务器实例
    server = get_grpc_server()

    # 注册拦截器
    if gateway_config.auth_enabled:
        # 注入共享 StreamClient 用于安全审计落 Redis Stream (audit:security)
        audit_stream = StreamClient()
        server.add_interceptor(
            AuthInterceptor(enabled=True, audit_stream=audit_stream)
        )
        logger.info("AuthInterceptor 已启用（含安全审计 Stream）")
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

    # 构建 LeaseStore (P3) —— 把真实 Lease 状态机注入 ControlService。
    lease_store: LeaseStore | None = None
    try:
        redis_client = await get_redis_client()
        if redis_client is not None:
            lease_store = LeaseStore(
                redis_client=redis_client,
                namespace=redis_namespace(),
                policy=LeasePolicy(),  # 默认 30s TTL / 10s renew
            )
            logger.info(
                "LeaseStore 已构建: namespace={} ttl_ms={} renew_after_ms={}",
                lease_store.namespace,
                lease_store.policy.ttl_ms,
                lease_store.policy.renew_after_ms,
            )
        else:
            logger.warning("Redis 不可用，ControlService 将退化为占位 Lease")
    except Exception as exc:  # pragma: no cover - defensive bootstrap
        logger.exception(f"构建 LeaseStore 失败，ControlService 将退化为占位 Lease: {exc}")

    # 注册服务实现：ControlService (lifecycle/lease/control) + DataService (tasks/status/logs)
    logger.info("注册 gRPC 服务")
    server.add_servicer(
        GatewayControlService(lease_store=lease_store),
        add_ControlServiceServicer_to_server,
    )
    server.add_servicer(GatewayDataService(), add_DataServiceServicer_to_server)
    logger.info("ControlService + DataService 已注册")

    # 启动服务器 —— P1-21: start() 失败返回 False,必须 fail-fast 退出,
    # 否则 pod 里进程还在但端口没 listen,healthcheck 只 pgrep 就误判 healthy,
    # Worker 打过来的连接会全部超时。
    if not await server.start():
        logger.error(
            "Gateway gRPC 服务器启动失败 (端口未绑定 / TLS 凭证缺失 / 明文被拒),"
            " 触发退出以让容器编排层重启并暴露给上游 healthcheck"
        )
        # DB 已在 init_db 里初始化过,退出前做一次 best-effort 关闭。
        try:
            await asyncio.wait_for(close_db(), timeout=5)
        except Exception:  # pragma: no cover - 退出路径的清理不阻塞
            logger.exception("退出前 close_db 失败,忽略")
        sys.exit(1)
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
