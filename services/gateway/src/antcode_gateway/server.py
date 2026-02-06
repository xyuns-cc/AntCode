"""
gRPC 服务器模块

提供 Gateway 端 gRPC 服务器实现，支持：
- TLS/mTLS 安全连接
- 认证拦截器
- 限流拦截器
- 优雅关闭
"""

from collections.abc import Callable
from concurrent import futures
from typing import Any

import grpc
from grpc import aio as grpc_aio
from loguru import logger

from antcode_gateway.config import GatewayConfig, gateway_config


class GrpcServer:
    """gRPC 服务器

    管理 gRPC 服务器的生命周期，包括启动、停止和服务注册。
    支持 TLS/mTLS、认证拦截器和限流拦截器。
    """

    def __init__(self, config: GatewayConfig | None = None):
        """初始化 gRPC 服务器

        Args:
            config: 服务器配置，默认使用全局配置
        """
        self.config = config or gateway_config
        self._server: grpc_aio.Server | None = None
        self._executor: futures.ThreadPoolExecutor | None = None
        self._started = False
        self._servicers: list[tuple[Any, Callable]] = []
        self._interceptors: list[grpc.aio.ServerInterceptor] = []

    @property
    def is_running(self) -> bool:
        """服务器是否正在运行"""
        return self._started and self._server is not None

    @property
    def host(self) -> str:
        """服务器地址"""
        return self.config.host

    @property
    def port(self) -> int:
        """服务器端口"""
        return self.config.port

    def add_interceptor(self, interceptor: grpc.aio.ServerInterceptor) -> None:
        """添加拦截器

        Args:
            interceptor: gRPC 拦截器
        """
        self._interceptors.append(interceptor)

    def add_servicer(self, servicer: Any, add_to_server_func: Callable) -> None:
        """添加服务实现

        Args:
            servicer: 服务实现实例
            add_to_server_func: 添加服务的函数（如 add_GatewayServiceServicer_to_server）
        """
        self._servicers.append((servicer, add_to_server_func))

    async def start(self) -> bool:
        """启动 gRPC 服务器

        Returns:
            是否成功启动
        """
        if not self.config.enabled:
            logger.info("gRPC 服务已禁用，跳过启动")
            return False

        if self._started:
            logger.warning("gRPC 服务器已在运行")
            return True

        try:
            # 创建服务器（带拦截器）
            self._executor = futures.ThreadPoolExecutor(max_workers=self.config.max_workers)
            self._server = grpc_aio.server(
                self._executor,
                options=self.config.server_options,
                interceptors=self._interceptors if self._interceptors else None,
            )

            # 注册所有服务
            for servicer, add_func in self._servicers:
                add_func(servicer, self._server)
                logger.debug(f"已注册服务: {servicer.__class__.__name__}")

            # 绑定端口
            listen_addr = f"[::]:{self.config.port}"

            if self.config.tls_enabled:
                # TLS/mTLS 模式
                credentials = self._create_server_credentials()
                if credentials is None:
                    logger.error("创建 TLS 凭证失败")
                    return False

                bound_port = self._server.add_secure_port(listen_addr, credentials)
                tls_mode = "mTLS" if self.config.mtls_enabled else "TLS"
                logger.info(f"gRPC 服务器启动于 {listen_addr} ({tls_mode})")
            else:
                # 非 TLS 模式
                bound_port = self._server.add_insecure_port(listen_addr)
                logger.info(f"gRPC 服务器启动于 {listen_addr} (insecure)")

            if bound_port == 0:
                logger.error(f"gRPC 服务器端口绑定失败: {listen_addr}")
                self._server = None
                return False

            # 启动服务器
            await self._server.start()
            self._started = True

            logger.info(
                f"gRPC 服务器已启动 - 端口: {self.config.port}, "
                f"最大工作线程: {self.config.max_workers}, "
                f"拦截器数: {len(self._interceptors)}"
            )
            return True

        except Exception as e:
            logger.error(f"gRPC 服务器启动失败: {e}")
            self._server = None
            self._started = False
            return False

    def _create_server_credentials(self) -> grpc.ServerCredentials | None:
        """创建服务器 TLS 凭证

        Returns:
            服务器凭证，失败返回 None
        """
        try:
            # 读取证书和密钥
            with open(self.config.tls_cert_path, "rb") as f:
                cert = f.read()
            with open(self.config.tls_key_path, "rb") as f:
                key = f.read()

            if self.config.mtls_enabled:
                # mTLS 模式：需要客户端证书
                with open(self.config.tls_ca_path, "rb") as f:
                    ca_cert = f.read()

                credentials = grpc.ssl_server_credentials(
                    [(key, cert)],
                    root_certificates=ca_cert,
                    require_client_auth=True,
                )
                logger.debug("已创建 mTLS 服务器凭证")
            else:
                # 单向 TLS 模式
                credentials = grpc.ssl_server_credentials([(key, cert)])
                logger.debug("已创建 TLS 服务器凭证")

            return credentials

        except FileNotFoundError as e:
            logger.error(f"TLS 证书文件不存在: {e}")
            return None
        except Exception as e:
            logger.error(f"创建 TLS 凭证失败: {e}")
            return None

    async def stop(self, grace: float | None = None) -> None:
        """停止 gRPC 服务器

        Args:
            grace: 优雅关闭等待时间（秒），默认使用配置值
        """
        if not self._started or self._server is None:
            logger.debug("gRPC 服务器未运行，无需停止")
            return

        grace = grace if grace is not None else self.config.shutdown_grace_period

        try:
            logger.info(f"正在停止 gRPC 服务器，优雅关闭等待 {grace} 秒...")
            await self._server.stop(grace)
            self._started = False
            self._server = None
            logger.info("gRPC 服务器已停止")

        except Exception as e:
            logger.error(f"gRPC 服务器停止失败: {e}")
            self._started = False
            self._server = None
        finally:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

    async def wait_for_termination(self) -> None:
        """等待服务器终止"""
        if self._server is not None:
            await self._server.wait_for_termination()


# 全局服务器实例
_grpc_server: GrpcServer | None = None


def get_grpc_server() -> GrpcServer:
    """获取全局 gRPC 服务器实例"""
    global _grpc_server
    if _grpc_server is None:
        _grpc_server = GrpcServer()
    return _grpc_server


def reset_grpc_server() -> None:
    """重置全局 gRPC 服务器实例（用于测试）"""
    global _grpc_server
    _grpc_server = None
