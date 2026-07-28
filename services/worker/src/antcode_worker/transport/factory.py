"""
传输层工厂

负责根据配置创建正确的传输层实例，并执行强制校验。

核心规则：
- mode=direct：必须有 redis_url；禁止出现 gateway 配置
- mode=gateway：必须有 gateway_host；禁止出现 redis_url
- 启动时打印明确的 Transport Banner
- 自检失败直接退出

Requirements: 7.2, 11.3
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, cast

from antcode_core.infrastructure.redis import (
    control_stream,
    redis_namespace,
    task_ready_stream,
    worker_group,
)
from loguru import logger

from antcode_worker.gateway_endpoint import (
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_PORT,
    GatewayAddress,
)
from antcode_worker.transport.base import TransportBase
from antcode_worker.transport.factory_components import (
    build_direct_control_client as _build_direct_control_client,
)
from antcode_worker.transport.factory_components import (
    build_gateway_transport_config as _build_gateway_transport_config,
)
from antcode_worker.transport.factory_components import (
    check_gateway_capabilities,
    decode_redis_text,
    gateway_auth_method,
    validate_direct_control_url,
    validate_gateway_security,
)
from antcode_worker.transport.factory_components import (
    resolve_gateway_address as _resolve_gateway_address,
)

if TYPE_CHECKING:
    from antcode_worker.transport.gateway import GatewayConfig


GATEWAY_AUTH_MTLS = "mtls"


class TransportConfigError(Exception):
    """传输层配置错误"""

    pass


resolve_gateway_address = partial(_resolve_gateway_address, error_type=TransportConfigError)


@dataclass
class DirectConfig:
    """Direct 模式配置"""

    redis_url: str = ""
    redis_password: str | None = None
    redis_namespace: str = redis_namespace()
    consumer_group: str = ""
    api_base_url: str = ""
    api_key: str = ""
    secret_key: str = ""
    allow_insecure_internal: bool = False
    reclaimed_queue_capacity: int = 10

    def __post_init__(self) -> None:
        self.redis_namespace = redis_namespace(self.redis_namespace)
        if not self.consumer_group:
            self.consumer_group = worker_group(self.redis_namespace)
        if isinstance(self.reclaimed_queue_capacity, bool) or self.reclaimed_queue_capacity < 1:
            raise ValueError("reclaimed_queue_capacity 必须是正整数")

    @property
    def task_stream_prefix(self) -> str:
        return task_ready_stream("", namespace=self.redis_namespace).rstrip(":") + ":"

    @property
    def control_stream_prefix(self) -> str:
        return control_stream("", namespace=self.redis_namespace).rstrip(":") + ":"


@dataclass
class GatewayConfigSpec:
    """Gateway 模式配置"""

    endpoint: str = ""  # host:port
    host: str = DEFAULT_GATEWAY_HOST
    port: int = DEFAULT_GATEWAY_PORT
    tls: bool = False
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    api_key: str | None = None


@dataclass
class TransportConfig:
    """传输层配置"""

    mode: str = "gateway"  # direct | gateway
    worker_id: str | None = None
    direct: DirectConfig = field(default_factory=DirectConfig)
    gateway: GatewayConfigSpec = field(default_factory=GatewayConfigSpec)


def validate_transport_config(config: TransportConfig) -> None:
    """
    强制校验传输层配置

    规则：
    - mode=direct：必须有 redis_url；禁止出现 gateway.endpoint
    - mode=gateway：必须有 gateway.host；禁止出现 redis_url

    Raises:
        TransportConfigError: 配置不合法
    """
    mode = config.mode.lower()

    if mode not in ("direct", "gateway"):
        raise TransportConfigError(f"无效的 transport.mode: '{mode}'，必须是 'direct' 或 'gateway'")

    if mode == "direct":
        # Direct 模式校验
        if not config.direct.redis_url:
            raise TransportConfigError("Direct 模式必须配置 redis_url\n示例: WORKER_REDIS_URL=redis://10.0.0.10:6379/0")
        # 禁止同时配置 gateway（非默认值）
        has_gateway_config = (
            config.gateway.endpoint
            or (config.gateway.host and config.gateway.host != DEFAULT_GATEWAY_HOST)
            or (config.gateway.port and config.gateway.port != DEFAULT_GATEWAY_PORT)
        )
        if has_gateway_config:
            raise TransportConfigError(
                "Direct 模式禁止配置 gateway.endpoint/host\n请移除 WORKER_GATEWAY_HOST 等 Gateway 相关配置"
            )
        if not all((config.direct.api_base_url, config.direct.api_key, config.direct.secret_key)):
            raise TransportConfigError("Direct 模式必须配置 Web API 地址和 Worker API/HMAC 凭据")
        _validate_direct_control_url(config.direct)

    elif mode == "gateway":
        # Gateway 模式校验
        if not config.gateway.host and not config.gateway.endpoint:
            raise TransportConfigError(
                "Gateway 模式必须配置 gateway.host 或 gateway.endpoint\n示例: WORKER_GATEWAY_HOST=gateway.example.com"
            )

        # Gateway Worker 不应拥有任何 Redis 凭证，包括 localhost 地址。
        if config.direct.redis_url:
            raise TransportConfigError(
                "Gateway 模式禁止配置 redis_url\n请移除 WORKER_REDIS_URL 配置，Gateway 模式下 Worker 不直连 Redis"
            )

        resolve_gateway_address(config.gateway)
        _validate_gateway_security(config.gateway)

    if not config.worker_id:
        raise TransportConfigError("必须配置 worker_id\n示例: WORKER_ID=worker-001，或使用安装 Key 注册生成凭证")


def _validate_direct_control_url(config: DirectConfig) -> None:
    try:
        validate_direct_control_url(config)
    except ValueError as exc:
        raise TransportConfigError(str(exc)) from exc


def _validate_gateway_security(config: GatewayConfigSpec) -> None:
    try:
        validate_gateway_security(config)
    except ValueError as exc:
        raise TransportConfigError(str(exc)) from exc


def print_transport_banner(config: TransportConfig) -> None:
    """
    打印传输层启动 Banner

    让运维一眼看出当前 Worker 的接入模式。
    """
    mode = config.mode.lower()

    banner_lines = [
        "",
        "=" * 60,
        "  AntCode Worker Transport Configuration",
        "=" * 60,
    ]

    if mode == "direct":
        # 隐藏密码
        redis_url = config.direct.redis_url
        if "@" in redis_url:
            # 格式: redis://:password@host:port/db
            parts = redis_url.split("@")
            redis_url = parts[0].rsplit(":", 1)[0] + ":***@" + parts[1]

        banner_lines.extend(
            [
                "  Mode:     DIRECT (内网直连 Redis)",
                f"  Redis:    {redis_url}",
                f"  Namespace:{redis_namespace(config.direct.redis_namespace)}",
                f"  Group:    {config.direct.consumer_group}",
                f"  Worker:   {config.worker_id}",
                "",
                "  WARN: Direct 模式仅限内网使用，请勿暴露 Redis 到公网",
            ]
        )

    elif mode == "gateway":
        endpoint = resolve_gateway_address(config.gateway).target
        tls_status = "ON" if config.gateway.tls else "OFF"
        auth_method = "mTLS" if gateway_auth_method(config.gateway) == GATEWAY_AUTH_MTLS else "API Key"

        banner_lines.extend(
            [
                "  Mode:     GATEWAY (公网 gRPC 接入)",
                f"  Endpoint: {endpoint}",
                f"  TLS:      {tls_status}",
                f"  Auth:     {auth_method}",
                f"  Worker:   {config.worker_id}",
                "",
                "  INFO: Gateway 模式下 Worker 不直连 Redis/MySQL",
            ]
        )

    banner_lines.extend(
        [
            "=" * 60,
            "",
        ]
    )

    for line in banner_lines:
        logger.info(line)


async def preflight_check_direct(config: TransportConfig) -> bool:
    """
    Direct 模式启动自检

    检查项：
    - PING Redis
    - XGROUP CREATE（若不存在）
    - XINFO GROUPS 只读验证消费者组
    """
    logger.info("执行 Direct 模式自检...")

    redis_client = None
    try:
        # T6-T1: 走统一 factory
        from antcode_core.infrastructure.redis.factory import (
            create_async_redis_client,
        )

        redis_client = create_async_redis_client(
            config.direct.redis_url,
            decode_responses=True,
        )

        # 1. PING
        await cast(Awaitable[bool], redis_client.ping())
        logger.info("  OK  Redis PING 成功")

        # 2. 检查/创建消费者组
        worker_id = config.worker_id
        if not worker_id:
            raise TransportConfigError("Direct 模式自检需要 worker_id")
        stream_key = task_ready_stream(worker_id, namespace=config.direct.redis_namespace)
        group_name = config.direct.consumer_group or worker_group(config.direct.redis_namespace)

        try:
            await redis_client.xgroup_create(stream_key, group_name, id="0", mkstream=True)
            logger.info(f"  OK  消费者组已创建: {group_name}")
        except Exception as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"  OK  消费者组已存在: {group_name}")
            else:
                raise

        # XREADGROUP 会领取真实任务；BLOCK 0 对新消息读取还会永久阻塞。
        # 读取 group 元数据即可验证 stream/group 的可见性，不改变 PEL。
        groups = await redis_client.xinfo_groups(stream_key)
        group_names = {decode_redis_text(group.get("name", group.get(b"name", ""))) for group in groups}
        if group_name not in group_names:
            raise RuntimeError(f"消费者组自检失败: {group_name}")
        logger.info(f"  OK  消费者组可读: {group_name}")

        logger.info("Direct 模式自检通过")
        return True

    except Exception:
        logger.exception("Direct 模式自检失败")
        logger.error("请检查 Redis 连接配置和网络连通性")
        return False
    finally:
        if redis_client is not None:
            await redis_client.aclose()


async def _check_gateway_capabilities(channel, config: TransportConfig) -> None:
    await check_gateway_capabilities(
        channel,
        auth_method=gateway_auth_method(config.gateway),
        api_key=config.gateway.api_key,
        worker_id=config.worker_id or "",
    )


async def preflight_check_gateway(config: TransportConfig) -> bool:
    """
    Gateway 模式启动自检

    检查项：
    - TLS 握手成功（证书/CA）
    - Auth 成功（API key / mTLS）
    - 无副作用的协议能力协商
    """
    logger.info("执行 Gateway 模式自检...")

    channel = None
    try:
        import grpc
        from grpc import aio as grpc_aio

        endpoint = resolve_gateway_address(config.gateway).target

        # 构建 channel options
        options = [
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
        ]

        # 创建 channel
        if config.gateway.tls:
            # 读取证书
            root_certs = None
            private_key = None
            certificate_chain = None

            if config.gateway.ca_cert:
                from pathlib import Path

                root_certs = Path(config.gateway.ca_cert).read_bytes()
                logger.info(f"  OK  CA 证书已加载: {config.gateway.ca_cert}")

            if config.gateway.client_cert and config.gateway.client_key:
                from pathlib import Path

                certificate_chain = Path(config.gateway.client_cert).read_bytes()
                private_key = Path(config.gateway.client_key).read_bytes()
                logger.info(f"  OK  客户端证书已加载: {config.gateway.client_cert}")

            credentials = grpc.ssl_channel_credentials(
                root_certificates=root_certs,
                private_key=private_key,
                certificate_chain=certificate_chain,
            )
            channel = grpc_aio.secure_channel(endpoint, credentials, options=options)
            logger.info("  OK  TLS 通道已创建")
        else:
            channel = grpc_aio.insecure_channel(endpoint, options=options)
            logger.info("  WARN 使用非 TLS 连接（仅限开发环境）")

        # 等待 channel 就绪
        await asyncio.wait_for(channel.channel_ready(), timeout=10.0)
        logger.info(f"  OK  gRPC 连接成功: {endpoint}")

        # 查询协议能力；不得在 preflight 中创建或轮换 lease。
        await _check_gateway_capabilities(channel, config)
        logger.info("  OK  Gateway 协议能力检查通过")

        logger.info("Gateway 模式自检通过")
        return True

    except TimeoutError:
        logger.error("Gateway 模式自检失败: 连接超时")
        logger.error("请检查 Gateway 地址和网络连通性")
        return False
    except Exception:
        logger.exception("Gateway 模式自检失败")
        logger.error("请检查 Gateway 配置、证书和网络连通性")
        return False
    finally:
        if channel is not None:
            await channel.close()


async def create_transport(
    config: TransportConfig,
    skip_validation: bool = False,
    skip_preflight: bool = False,
    exit_on_failure: bool = True,
) -> TransportBase:
    """
    创建传输层实例

    Args:
        config: 传输层配置
        skip_validation: 跳过配置校验（仅测试用）
        skip_preflight: 跳过启动自检（仅测试用）
        exit_on_failure: 自检失败时退出进程

    Returns:
        传输层实例

    Raises:
        TransportConfigError: 配置不合法
        SystemExit: 自检失败且 exit_on_failure=True
    """
    # 1. 配置校验
    if not skip_validation:
        validate_transport_config(config)

    # 2. 打印 Banner
    print_transport_banner(config)

    mode = config.mode.lower()

    # 3. 启动自检
    if not skip_preflight:
        if mode == "direct":
            success = await preflight_check_direct(config)
        else:
            success = await preflight_check_gateway(config)

        if not success:
            logger.error("传输层自检失败，Worker 无法启动")
            if exit_on_failure:
                sys.exit(1)
            raise TransportConfigError("传输层自检失败")

    # 4. 创建传输层实例
    if mode == "direct":
        from antcode_worker.transport.redis import RedisTransport

        return RedisTransport(
            redis_url=config.direct.redis_url,
            worker_id=config.worker_id,
            namespace=config.direct.redis_namespace,
            consumer_group=config.direct.consumer_group or worker_group(config.direct.redis_namespace),
            direct_control=build_direct_control_client(config.direct, config.worker_id or ""),
            reclaimed_queue_capacity=config.direct.reclaimed_queue_capacity,
        )

    else:  # gateway
        from antcode_worker.transport.gateway import GatewayTransport

        gateway_config = build_gateway_transport_config(config)

        transport = GatewayTransport(gateway_config=gateway_config)
        if config.worker_id:
            transport.set_credentials(worker_id=config.worker_id)

        return transport


def build_gateway_transport_config(config: TransportConfig) -> GatewayConfig:
    return _build_gateway_transport_config(
        spec=config.gateway,
        address=resolve_gateway_address(config.gateway),
        auth_method=gateway_auth_method(config.gateway),
        worker_id=config.worker_id or "",
    )


def build_direct_control_client(config: DirectConfig, worker_id: str):
    return _build_direct_control_client(config, worker_id)


def build_transport_config_from_env(
    transport_mode: str | None = None,
    worker_id: str | None = None,
    redis_url: str | None = None,
    gateway_host: str | None = None,
    gateway_port: int | None = None,
    gateway_tls: bool | None = None,
    api_key: str | None = None,
    ca_cert: str | None = None,
    client_cert: str | None = None,
    client_key: str | None = None,
) -> TransportConfig:
    """
    从环境变量/参数构建传输层配置

    优先级：参数 > 环境变量 > 默认值
    """
    import os

    # 读取环境变量
    mode = transport_mode or os.getenv("WORKER_TRANSPORT_MODE") or "gateway"
    wid = worker_id or os.getenv("WORKER_ID")

    config = TransportConfig(
        mode=mode,
        worker_id=wid,
    )

    # Direct 配置
    config.direct.redis_url = redis_url or os.getenv("WORKER_REDIS_URL") or ""
    config.direct.redis_namespace = redis_namespace(
        os.getenv("WORKER_REDIS_NAMESPACE") or config.direct.redis_namespace
    )
    config.direct.consumer_group = os.getenv(
        "WORKER_CONSUMER_GROUP",
        worker_group(config.direct.redis_namespace),
    )
    config.direct.api_base_url = os.getenv("WORKER_API_BASE_URL", "")
    config.direct.api_key = api_key or os.getenv("WORKER_API_KEY") or os.getenv("WORKER_CREDENTIAL_API_KEY") or ""
    config.direct.secret_key = os.getenv("WORKER_CREDENTIAL_SECRET_KEY", "")
    config.direct.allow_insecure_internal = os.getenv("WORKER_API_ALLOW_INSECURE_INTERNAL", "").lower() in (
        "true",
        "1",
        "yes",
    )

    # Gateway 配置
    config.gateway.endpoint = os.getenv("WORKER_GATEWAY_ENDPOINT") or ""
    config.gateway.host = gateway_host or os.getenv("WORKER_GATEWAY_HOST") or DEFAULT_GATEWAY_HOST
    config.gateway.port = (
        gateway_port if gateway_port is not None else int(os.getenv("WORKER_GATEWAY_PORT", str(DEFAULT_GATEWAY_PORT)))
    )
    env_gateway_tls = os.getenv("WORKER_GATEWAY_TLS", "").lower() in ("true", "1", "yes")
    config.gateway.tls = env_gateway_tls if gateway_tls is None else gateway_tls
    config.gateway.api_key = api_key or os.getenv("WORKER_API_KEY")
    config.gateway.ca_cert = ca_cert or os.getenv("WORKER_CA_CERT")
    config.gateway.client_cert = client_cert or os.getenv("WORKER_CLIENT_CERT")
    config.gateway.client_key = client_key or os.getenv("WORKER_CLIENT_KEY")

    return config


__all__ = [
    "TransportConfig",
    "TransportConfigError",
    "DirectConfig",
    "GatewayConfigSpec",
    "GatewayAddress",
    "resolve_gateway_address",
    "gateway_auth_method",
    "build_gateway_transport_config",
    "build_direct_control_client",
    "validate_transport_config",
    "print_transport_banner",
    "preflight_check_direct",
    "preflight_check_gateway",
    "create_transport",
    "build_transport_config_from_env",
]
