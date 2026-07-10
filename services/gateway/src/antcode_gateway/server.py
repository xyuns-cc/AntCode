"""
gRPC 服务器模块

提供 Gateway 端 gRPC 服务器实现，支持：
- TLS/mTLS 安全连接
- 认证拦截器
- 限流拦截器
- 优雅关闭
"""

import asyncio
import os
from collections.abc import Callable
from typing import Any
from concurrent import futures

import grpc
from grpc import aio as grpc_aio
from loguru import logger

from antcode_gateway.config import GatewayConfig, gateway_config

# P1-21: 引入标准 gRPC 健康检查(grpc.health.v1)——Dockerfile 里带的
# grpc_health_probe 需要服务端真的注册了 HealthServicer 才能拿到
# SERVING/NOT_SERVING。包不存在时静默退化,避免离线测试环境构建失败。
#
# P2-04: 原先 config.server_options 里 grpc.max_connection_idle_ms=0 会让
# grpc_health_probe 短连接被判定为 idle 直接 GOAWAY, 已经改成 300_000
# (5min), grpc_health_probe 探针可以直接用了。Dockerfile HEALTHCHECK 当前
# 仍走独立的 HTTP /health/ready (顺便覆盖 Redis / DB 探活), 后续如果只需
# gRPC 侧健康态, 可以无缝切回 grpc_health_probe -addr=localhost:50051。
try:
    from grpc_health.v1 import health, health_pb2, health_pb2_grpc

    _HEALTH_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失回退
    health = None  # type: ignore[assignment]
    health_pb2 = None  # type: ignore[assignment]
    health_pb2_grpc = None  # type: ignore[assignment]
    _HEALTH_AVAILABLE = False


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
        # grpc.health.v1 HealthServicer 实例(SERVING/NOT_SERVING 由启动/停止流程写入)
        self._health_servicer: Any | None = None
        # 独立 HTTP readiness 端点(与 gRPC 端口解耦,避免受 keepalive 影响)
        self._readiness_server: asyncio.base_events.Server | None = None
        self._readiness_host: str = os.getenv("READINESS_HOST", "0.0.0.0")
        self._readiness_port: int = int(os.getenv("READINESS_PORT", "8100"))
        self._readiness_enabled: bool = os.getenv(
            "READINESS_HTTP_ENABLED", "true"
        ).lower() == "true"

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
            add_to_server_func: 添加服务的函数（如 add_ControlServiceServicer_to_server）
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

            # 注册 grpc.health.v1.Health —— 让 grpc_health_probe / L7 探针
            # 拿到真实 SERVING/NOT_SERVING 状态。auth.py / rate_limit.py 已把
            # /grpc.health.v1.Health/Check 加入白名单,不会被拦截器截掉。
            if _HEALTH_AVAILABLE:
                self._health_servicer = health.HealthServicer(
                    experimental_non_blocking=True,
                    experimental_thread_pool=self._executor,
                )
                health_pb2_grpc.add_HealthServicer_to_server(
                    self._health_servicer, self._server
                )
                logger.debug("已注册 grpc.health.v1.Health 服务")
            else:
                logger.warning(
                    "grpcio-health-checking 未安装,grpc_health_probe 探针会失败 "
                    "(容器镜像里应已通过 pip 装好)"
                )

            # 绑定端口
            listen_addr = f"[::]:{self.config.port}"

            if self.config.tls_enabled:
                # TLS/mTLS 模式
                credentials = self._create_server_credentials()
                if credentials is None:
                    logger.error("创建 TLS 凭证失败")
                    self._server = None
                    self._health_servicer = None
                    return False

                bound_port = self._server.add_secure_port(listen_addr, credentials)
                tls_mode = "mTLS" if self.config.mtls_enabled else "TLS"
                logger.info(f"gRPC 服务器启动于 {listen_addr} ({tls_mode})")
            else:
                # 非 TLS 模式
                # D4: 启用鉴权（默认 true）却走明文端口，意味着 api_key/JWT 会明文
                # 在网络上传输。除非显式声明 dev 场景，否则拒绝启动 fail-closed。
                if self.config.auth_enabled and not self.config.allow_insecure_with_auth:
                    logger.error(
                        "gRPC 明文端口启动被拒绝：AUTH_ENABLED=true 时凭证会明文传输。"
                        " 请配置 GRPC_TLS_CERT_PATH+GRPC_TLS_KEY_PATH 或显式设置"
                        " ANTCODE_GATEWAY_ALLOW_INSECURE=true 声明本地/测试环境。"
                    )
                    self._server = None
                    self._health_servicer = None
                    return False
                bound_port = self._server.add_insecure_port(listen_addr)
                logger.warning(
                    "gRPC 服务器启动于 %s (insecure — 凭证会明文传输，仅限本地/测试)",
                    listen_addr,
                )

            if bound_port == 0:
                logger.error(f"gRPC 服务器端口绑定失败: {listen_addr}")
                self._server = None
                self._health_servicer = None
                return False

            # 启动服务器
            await self._server.start()
            self._started = True

            # 标记 SERVING —— 只有端口真正 listen 之后才置为 SERVING,
            # 避免 healthcheck 在启动过程中就返回 healthy(P1-21 假 healthy 修复)。
            if self._health_servicer is not None:
                # 空字符串 "" 是标准的整体健康状态 key(grpc_health_probe 默认查询)
                self._health_servicer.set(
                    "", health_pb2.HealthCheckResponse.SERVING
                )
                # 同时把已注册的业务服务也标记为 SERVING,方便按 service 查询
                for servicer, _ in self._servicers:
                    service_name = servicer.__class__.__name__
                    try:
                        self._health_servicer.set(
                            service_name, health_pb2.HealthCheckResponse.SERVING
                        )
                    except Exception:  # pragma: no cover - 防御性容错
                        pass

            logger.info(
                f"gRPC 服务器已启动 - 端口: {self.config.port}, "
                f"最大工作线程: {self.config.max_workers}, "
                f"拦截器数: {len(self._interceptors)}"
            )

            # P1-21: 启动独立 HTTP readiness endpoint 供 Docker HEALTHCHECK 使用
            await self._start_readiness_endpoint()

            return True

        except Exception as e:
            logger.exception(f"gRPC 服务器启动失败: {e}")
            self._server = None
            self._started = False
            self._health_servicer = None
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
            logger.exception(f"创建 TLS 凭证失败: {e}")
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
            # 先把 Health 状态置为 NOT_SERVING,让 LB / probe 立刻把这台摘掉,
            # 避免优雅关闭窗口里继续被路由流量。
            if self._health_servicer is not None:
                try:
                    self._health_servicer.enter_graceful_shutdown()
                except Exception:  # pragma: no cover - 防御性容错
                    logger.exception("HealthServicer.enter_graceful_shutdown 失败")
            # 关闭 HTTP readiness (放在 gRPC stop 前,让 K8s/HEALTHCHECK 立刻感知)
            await self._stop_readiness_endpoint()
            await self._server.stop(grace)
            self._started = False
            self._server = None
            self._health_servicer = None
            logger.info("gRPC 服务器已停止")

        except Exception as e:
            logger.exception(f"gRPC 服务器停止失败: {e}")
            self._started = False
            self._server = None
            self._health_servicer = None
        finally:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

    async def wait_for_termination(self) -> None:
        """等待服务器终止"""
        if self._server is not None:
            await self._server.wait_for_termination()

    # ------------------------------------------------------------------
    # P1-21: HTTP readiness endpoint —— 独立于 gRPC 端口
    # ------------------------------------------------------------------
    # Docker HEALTHCHECK 走 `curl -f http://localhost:<READINESS_PORT>/health/ready`,
    # 端口 listen 才代表 start() 走到了 self._started=True 分支;pgrep 那种"进程还活着
    # 就说 healthy"的假阳性被彻底消除。
    #
    # 只用 stdlib asyncio.start_server,不引入 aiohttp 依赖;
    # gateway pyproject.toml 里没有 aiohttp,也不打算为一个简易探针拉一堆包。

    async def _check_redis_ready(self) -> tuple[bool, str]:
        """检查 Redis 是否可用 (轻量 ping,3s 上限)"""
        try:
            from antcode_core.infrastructure.redis import get_redis_client

            client = await asyncio.wait_for(get_redis_client(), timeout=3.0)
            if client is None:
                return False, "redis_client_none"
            await asyncio.wait_for(client.ping(), timeout=3.0)
            return True, "ok"
        except Exception as exc:  # pragma: no cover - 依赖运行时环境
            return False, f"redis_error:{type(exc).__name__}"

    async def _check_db_ready(self) -> tuple[bool, str]:
        """检查 DB 是否可用 (SELECT 1,3s 上限)"""
        try:
            from tortoise import Tortoise

            # tortoise 未初始化时 apps 为空,直接判失败
            if not Tortoise._inited:  # type: ignore[attr-defined]
                return False, "db_not_inited"
            conn = Tortoise.get_connection("default")
            await asyncio.wait_for(conn.execute_query("SELECT 1"), timeout=3.0)
            return True, "ok"
        except Exception as exc:  # pragma: no cover - 依赖运行时环境
            return False, f"db_error:{type(exc).__name__}"

    async def _readiness_response(self) -> tuple[int, str]:
        """
        计算 /health/ready 响应:
        - gRPC server 未 listen → 503
        - Redis / DB 任一失败 → 503(可用性由具体错误标注)
        - 全部通过 → 200
        """
        parts: list[str] = []
        healthy = True

        grpc_ok = self.is_running
        parts.append(f"grpc={'ok' if grpc_ok else 'down'}")
        if not grpc_ok:
            healthy = False

        redis_ok, redis_reason = await self._check_redis_ready()
        parts.append(f"redis={redis_reason}")
        if not redis_ok:
            healthy = False

        db_ok, db_reason = await self._check_db_ready()
        parts.append(f"db={db_reason}")
        if not db_ok:
            healthy = False

        body = "{" + ",".join(f'"{p.split("=", 1)[0]}":"{p.split("=", 1)[1]}"' for p in parts) + "}"
        status = 200 if healthy else 503
        return status, body

    async def _handle_readiness_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """极简 HTTP/1.1 GET 处理器 —— 只识别 /health/ready,其他一律 404。"""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=3.0)
            if not request_line:
                writer.close()
                return
            try:
                method, path, _version = request_line.decode("ascii", errors="replace").split()
            except ValueError:
                method, path = b"", b""  # type: ignore[assignment]

            # 读取并丢弃 headers 直到空行
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                if not line or line in (b"\r\n", b"\n"):
                    break

            if method != "GET":
                body = "method not allowed"
                writer.write(
                    (
                        "HTTP/1.1 405 Method Not Allowed\r\n"
                        "Content-Type: text/plain\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n\r\n"
                        f"{body}"
                    ).encode()
                )
            elif path in ("/health/ready", "/healthz", "/health"):
                status, body = await self._readiness_response()
                reason = "OK" if status == 200 else "Service Unavailable"
                writer.write(
                    (
                        f"HTTP/1.1 {status} {reason}\r\n"
                        "Content-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n\r\n"
                        f"{body}"
                    ).encode()
                )
            else:
                body = "not found"
                writer.write(
                    (
                        "HTTP/1.1 404 Not Found\r\n"
                        "Content-Type: text/plain\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n\r\n"
                        f"{body}"
                    ).encode()
                )
            await writer.drain()
        except Exception:  # pragma: no cover - 防御性,避免探针连接把整个 gateway 拖挂
            logger.exception("readiness endpoint 请求处理失败")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # pragma: no cover
                pass

    async def _start_readiness_endpoint(self) -> None:
        """启动 /health/ready HTTP server (独立于 gRPC 端口)"""
        if not self._readiness_enabled:
            logger.info("readiness HTTP endpoint 已禁用 (READINESS_HTTP_ENABLED=false)")
            return
        try:
            self._readiness_server = await asyncio.start_server(
                self._handle_readiness_request,
                host=self._readiness_host,
                port=self._readiness_port,
            )
            logger.info(
                "readiness HTTP endpoint 已启动: http://{}:{}/health/ready",
                self._readiness_host,
                self._readiness_port,
            )
        except Exception:
            # 探针端口起不来不应阻止 gRPC 主流程,但要显式告警。
            logger.exception(
                "readiness HTTP endpoint 启动失败 (host={} port={}),Dockerfile HEALTHCHECK 会误判 unhealthy",
                self._readiness_host,
                self._readiness_port,
            )
            self._readiness_server = None

    async def _stop_readiness_endpoint(self) -> None:
        """停止 /health/ready HTTP server"""
        if self._readiness_server is None:
            return
        try:
            self._readiness_server.close()
            await self._readiness_server.wait_closed()
            logger.info("readiness HTTP endpoint 已停止")
        except Exception:  # pragma: no cover
            logger.exception("readiness HTTP endpoint 停止失败")
        finally:
            self._readiness_server = None


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
