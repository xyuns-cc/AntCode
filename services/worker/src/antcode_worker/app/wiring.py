"""
依赖注入容器

负责组装 Worker 的所有组件。

Requirements: 2.4
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class Container:
    """
    依赖注入容器

    管理 Worker 所有组件的生命周期和依赖关系。

    Requirements: 2.4
    """

    # 配置
    config: Any = None

    # 核心组件
    transport: Any = None
    engine: Any = None
    runtime_manager: Any = None
    executor: Any = None
    plugin_registry: Any = None
    log_manager: Any = None
    heartbeat_reporter: Any = None

    # 可观测性
    metrics_server: Any = None
    health_server: Any = None
    observability_server: Any = None
    metrics_collector: Any = None

    # 项目与产物
    project_fetcher: Any = None
    artifact_manager: Any = None

    # 安全
    identity: Any = None
    secrets: Any = None

    # 状态
    _initialized: bool = False
    _components: dict[str, Any] = field(default_factory=dict)

    def register(self, name: str, component: Any) -> None:
        """注册组件"""
        self._components[name] = component
        setattr(self, name, component)
        logger.debug(f"组件已注册: {name}")

    def get(self, name: str) -> Any | None:
        """获取组件"""
        return self._components.get(name) or getattr(self, name, None)

    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized

    def mark_initialized(self) -> None:
        """标记为已初始化"""
        self._initialized = True


@dataclass(frozen=True)
class _TransportBootstrap:
    worker_id: str | None
    gateway_host: str
    gateway_port: int
    api_key: str | None
    credentials: Any
    credential_service: Any


def create_container(config: Any) -> Container:
    """
    创建并配置依赖容器

    Args:
        config: Worker 配置

    Returns:
        配置好的容器
    """
    container = Container(config=config)

    # T6-T4a: plugin_registry 提前创建，供 register-direct 上报 capabilities.task_types。
    # 原顺序里 transport 内部 register-direct 时 registry 还没构建，无法读能力。
    plugin_registry = _create_plugin_registry(config)
    container.register("plugin_registry", plugin_registry)
    from antcode_worker.app.capabilities import detect_plugin_capabilities

    capabilities = detect_plugin_capabilities(plugin_registry)

    # 1. 创建传输层
    transport = _create_transport(config)
    if hasattr(transport, "set_capabilities"):
        transport.set_capabilities(capabilities)
    container.register("transport", transport)

    # 2. 创建运行时管理器
    runtime_manager = _create_runtime_manager(config)
    container.register("runtime_manager", runtime_manager)

    # 3. 创建执行器
    executor = _create_executor(config)
    container.register("executor", executor)

    # 5. 创建日志管理器工厂
    log_manager = _create_log_manager(config, transport)
    container.register("log_manager", log_manager)

    # 6. 创建指标采集器
    metrics_collector = _create_metrics_collector(config)
    container.register("metrics_collector", metrics_collector)

    # 7. 创建心跳上报器
    heartbeat_reporter = _create_heartbeat_reporter(config, transport, metrics_collector)
    container.register("heartbeat_reporter", heartbeat_reporter)

    # 8. 创建项目获取器与产物管理器
    project_fetcher = _create_project_fetcher(config, transport)
    container.register("project_fetcher", project_fetcher)
    artifact_manager = _create_artifact_manager(config, transport)
    container.register("artifact_manager", artifact_manager)

    # 9. 创建引擎（依赖其他组件）
    engine = _create_engine(
        config=config,
        transport=transport,
        runtime_manager=runtime_manager,
        executor=executor,
        plugin_registry=plugin_registry,
        log_manager=log_manager,
        project_fetcher=project_fetcher,
        artifact_manager=artifact_manager,
        tombstone_redis=_create_tombstone_redis(config),
    )
    container.register("engine", engine)

    # 11. 创建可观测性服务器
    observability_server = _create_observability_server(config, transport, engine)
    container.register("observability_server", observability_server)

    container.mark_initialized()
    logger.info("依赖容器初始化完成")

    return container


def _create_transport(config: Any) -> Any:
    """创建传输层

    使用新的工厂模块，支持：
    - 强制二选一配置校验
    - 启动 Banner 打印
    - 启动自检（可选）
    """
    from antcode_worker.transport.factory import (
        DirectConfig,
        GatewayConfigSpec,
        TransportConfig,
        build_direct_control_client,
        build_gateway_transport_config,
    )

    # 构建传输层配置
    transport_mode = getattr(config, "transport_mode", "gateway")

    bootstrap = _prepare_transport_bootstrap(config, transport_mode)
    worker_id = bootstrap.worker_id
    credentials = bootstrap.credentials
    if transport_mode == "direct":
        worker_id, credentials = _prepare_direct_transport(config, bootstrap)

    # 构建配置对象
    transport_config = TransportConfig(
        mode=transport_mode,
        worker_id=worker_id,
        direct=DirectConfig(
            redis_url=_build_authenticated_redis_url(
                getattr(config, "redis_url", ""),
                credentials,
            ),
            redis_namespace=getattr(config, "redis_namespace", "antcode"),
            api_base_url=_normalize_api_base_url(
                getattr(config, "api_base_url", ""),
                bootstrap.gateway_host,
                allow_insecure_internal=getattr(config, "api_allow_insecure_internal", False),
            ),
            api_key=str(getattr(credentials, "api_key", "") or ""),
            secret_key=str(getattr(credentials, "secret_key", "") or ""),
            allow_insecure_internal=getattr(config, "api_allow_insecure_internal", False),
            reclaimed_queue_capacity=max(1, int(getattr(config, "max_concurrent_tasks", 5))) * 2,
        ),
        gateway=(
            GatewayConfigSpec(
                host=bootstrap.gateway_host,
                port=bootstrap.gateway_port,
                tls=getattr(config, "gateway_tls", False),
                ca_cert=getattr(config, "ca_cert", None),
                client_cert=getattr(config, "client_cert", None),
                client_key=getattr(config, "client_key", None),
                api_key=bootstrap.api_key,
            )
            if transport_mode == "gateway"
            else GatewayConfigSpec()
        ),
    )

    # 使用工厂创建传输层（同步包装）
    # 注意：自检需要异步，这里先跳过，在 lifecycle 中执行
    from antcode_worker.transport.factory import (
        TransportConfigError,
        print_transport_banner,
        validate_transport_config,
    )

    try:
        validate_transport_config(transport_config)
    except TransportConfigError as e:
        logger.error(f"传输层配置错误: {e}")
        raise

    print_transport_banner(transport_config)

    # 创建传输层实例
    if transport_mode == "direct":
        from antcode_worker.transport.redis import RedisTransport

        return RedisTransport(
            redis_url=transport_config.direct.redis_url,
            worker_id=worker_id,
            namespace=transport_config.direct.redis_namespace,
            consumer_group=transport_config.direct.consumer_group,
            direct_control=build_direct_control_client(transport_config.direct, worker_id or ""),
        )
    else:
        from antcode_worker.transport.gateway import GatewayTransport

        # P2-20: worker_id 必须在 GatewayConfig 构造时就写入,后续
        # deregister() 会读 ``_gateway_config.worker_id`` 来发 Deregister RPC。
        # ``set_credentials`` 只是二次兜底(比如 api_key 后置注入的场景),
        # 真正的 single source of truth 是构造参数里的 ``worker_id``。
        gateway_config = build_gateway_transport_config(transport_config)
        transport = GatewayTransport(gateway_config=gateway_config)
        if worker_id:
            # 显式再走一次 set_credentials,让 ``_gateway_config.worker_id``
            # 一定与 wiring 拿到的 worker_id 保持一致(防御式,即使上面
            # dataclass 构造有默认值覆盖也能纠正)。
            transport.set_credentials(worker_id=worker_id)
        return transport


def _prepare_transport_bootstrap(config: Any, transport_mode: str) -> _TransportBootstrap:
    import os

    from antcode_core.common.config import settings

    if transport_mode == "direct" and not settings.REDIS_ACL_ENABLED:
        from antcode_worker.transport.factory import TransportConfigError

        raise TransportConfigError("Direct 模式必须启用 REDIS_ACL_ENABLED，禁止使用共享 Redis 凭据")

    from antcode_worker.services.credential import get_credential_store, init_credential_service

    store = get_credential_store(
        getattr(config, "credential_store", "persistent"),
        Path(getattr(config, "data_dir")),
    )
    credential_service = init_credential_service(store)
    credentials = credential_service.load()
    from antcode_worker.app.worker_registration import resume_registration_ack

    resume_registration_ack(credential_service, credentials)
    credentials = _require_control_credentials(
        config,
        credential_service,
        credentials,
        required=transport_mode in ("gateway", "direct"),
    )
    env_worker_id = os.getenv("WORKER_ID")
    worker_id = env_worker_id or getattr(credentials, "worker_id", None)
    if transport_mode == "direct" and env_worker_id and env_worker_id != credentials.worker_id:
        raise RuntimeError("WORKER_ID 与安装 Key 注册身份不匹配")
    gateway_host = getattr(credentials, "gateway_host", "") or getattr(config, "gateway_host", "localhost")
    gateway_port = getattr(credentials, "gateway_port", 0) or getattr(config, "gateway_port", 50051)
    api_key = getattr(config, "api_key", None) or os.getenv("WORKER_API_KEY")
    api_key = api_key or getattr(credentials, "api_key", None)
    return _TransportBootstrap(
        worker_id=worker_id,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        api_key=api_key,
        credentials=credentials,
        credential_service=credential_service,
    )


def _require_control_credentials(
    config: Any,
    credential_service: Any,
    credentials: Any,
    *,
    required: bool,
) -> Any:
    if not required or (credentials and credentials.is_valid()):
        return credentials
    credentials = _register_by_install_key(config=config, credential_service=credential_service)
    if credentials and credentials.is_valid():
        return credentials
    from antcode_worker.transport.factory import TransportConfigError

    message = "Gateway 或 Direct ACL 模式首次启动必须配置安装 Key\n示例: ANTCODE_WORKER_KEY=xxx"
    raise TransportConfigError(message)


def _prepare_direct_transport(config: Any, bootstrap: _TransportBootstrap) -> tuple[str, Any]:
    worker_id = bootstrap.worker_id
    if not worker_id:
        raise RuntimeError("Direct 模式缺少可信控制面签发的 worker_id")
    credentials = _issue_direct_redis_acl(
        config=config,
        credentials=bootstrap.credentials,
        credential_service=bootstrap.credential_service,
    )
    return credentials.worker_id, credentials


def _build_authenticated_redis_url(base_url: str, credentials: Any | None) -> str:
    """Inject the atomically persisted per-Worker Redis credentials."""
    from urllib.parse import urlsplit, urlunsplit

    if not base_url:
        return base_url
    username = getattr(credentials, "redis_username", "")
    password = getattr(credentials, "redis_password", "")
    if not username or not password:
        return base_url
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    netloc = f"{username}:{password}@{host}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _normalize_api_base_url(
    value: str | None,
    gateway_host: str,
    *,
    allow_insecure_internal: bool = False,
) -> str:
    from antcode_worker.app.worker_registration import normalize_api_base_url

    return normalize_api_base_url(
        value,
        gateway_host,
        allow_insecure_internal=allow_insecure_internal,
    )


def _should_trust_env_proxy(api_base_url: str) -> bool:
    try:
        from urllib.parse import urlparse

        host = urlparse(api_base_url).hostname
    except Exception:
        return True
    if not host:
        return True
    host = host.lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return False
    return True


def _register_by_install_key(
    config: Any,
    credential_service: Any,
):
    from antcode_worker.app.worker_registration import register_by_install_key

    return register_by_install_key(config, credential_service)


def _issue_direct_redis_acl(
    *,
    config: Any,
    credentials: Any | None,
    credential_service: Any,
):
    """Rotate Direct Redis ACL credentials through the signed Web API."""
    credential_service.ensure_durable_writable()
    with credential_service.registration_session():
        current = credential_service.load()
        if current is None or not current.is_valid():
            raise RuntimeError("Direct ACL 签发缺少有效的 Worker API/HMAC 凭据")
        if credentials is not None and current.worker_id != credentials.worker_id:
            raise RuntimeError("Direct ACL 签发期间 Worker 身份发生变化")
        return _rotate_direct_redis_acl(config, current, credential_service)


def _rotate_direct_redis_acl(config: Any, credentials: Any, credential_service: Any):
    from types import SimpleNamespace

    import httpx
    from antcode_core.common.utils.worker_request import build_worker_signed_headers

    api_base_url = _normalize_api_base_url(
        getattr(config, "api_base_url", ""),
        getattr(config, "gateway_host", "localhost"),
        allow_insecure_internal=getattr(config, "api_allow_insecure_internal", False),
    )
    url = f"{api_base_url}/api/v1/workers/{credentials.worker_id}/redis-acl/issue"
    headers = build_worker_signed_headers(
        SimpleNamespace(public_id=credentials.worker_id),
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        payload={},
    )
    with httpx.Client(timeout=15.0, trust_env=_should_trust_env_proxy(api_base_url)) as client:
        response = client.post(url, json={}, headers=headers)
    body = _require_success_response(response, operation="Direct Redis ACL 签发")
    data = body.get("data") or {}
    username = str(data.get("redis_username") or "")
    password = str(data.get("redis_password") or "")
    if not username or not password:
        raise RuntimeError("Direct Redis ACL 签发返回数据不完整")
    updated = replace(credentials, redis_username=username, redis_password=password)
    if not credential_service.save(updated):
        raise RuntimeError("Direct Redis ACL 已轮换，但新凭据持久化失败")
    return updated


def _require_success_response(response: Any, *, operation: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{operation}返回非 JSON 响应") from exc
    if response.status_code >= 400:
        detail = body.get("message") or body.get("detail") if isinstance(body, dict) else None
        raise RuntimeError(detail or f"{operation}失败 (HTTP {response.status_code})")
    if not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(message or f"{operation}失败")
    return body


def _create_runtime_manager(config: Any) -> Any:
    """创建运行时管理器"""
    import os

    from antcode_worker.config import DATA_ROOT
    from antcode_worker.runtime.manager import RuntimeManager, RuntimeManagerConfig
    from antcode_worker.runtime.uv_manager import uv_manager

    data_dir = getattr(config, "data_dir", str(DATA_ROOT))
    venvs_dir = getattr(config, "venvs_dir", None) or os.path.join(data_dir, "runtimes")
    locks_dir = getattr(config, "locks_dir", None)
    uv_cache_dir = getattr(config, "uv_cache_dir", None)

    manager_config = RuntimeManagerConfig(
        venvs_dir=venvs_dir,
        locks_dir=locks_dir,
        uv_cache_dir=uv_cache_dir,
    )
    uv_manager.set_venvs_dir(venvs_dir)
    return RuntimeManager(manager_config)


def _create_executor(config: Any) -> Any:
    """创建执行器。

    P0-a5: 按 config.sandbox_mode 分派 ProcessExecutor(默认,兼容) 或
    SandboxExecutor(生产推荐)。之前 wiring 硬编码 ProcessExecutor,导致
    SandboxExecutor 虽定义但从不被使用。
    """
    import os
    import shlex
    import shutil

    from antcode_worker.executor import (
        ExecutorConfig,
        SandboxConfig,
        SandboxExecutor,
    )

    max_concurrent = getattr(config, "max_concurrent_tasks", 5)
    default_timeout = getattr(config, "task_timeout", 3600)
    cpu_limit = getattr(config, "task_cpu_time_limit_sec", 0)
    memory_limit = getattr(config, "task_memory_limit_mb", 0)
    enforce_rlimit = bool(getattr(config, "sandbox_enforce_rlimit", True))
    max_open_files = int(getattr(config, "sandbox_max_open_files", 256))
    max_processes = int(getattr(config, "sandbox_max_processes", 64))

    exec_config = ExecutorConfig(
        max_concurrent=max_concurrent,
        default_timeout=default_timeout,
        default_cpu_limit_seconds=cpu_limit if cpu_limit > 0 else 0,
        default_memory_limit_mb=memory_limit if memory_limit > 0 else 0,
        enforce_rlimit=enforce_rlimit,
        default_max_open_files=max_open_files,
        default_max_processes=max_processes,
    )

    sandbox_mode = str(getattr(config, "sandbox_mode", "sandbox") or "sandbox").strip().lower()
    if sandbox_mode == "sandbox":
        # 解析可选的外接沙箱命令(如 firejail / bwrap)
        sandbox_command_str = str(getattr(config, "sandbox_command", "") or "").strip()
        sandbox_command_list: list[str] | None = None
        if not sandbox_command_str:
            raise RuntimeError(
                "sandbox_mode=sandbox 必须配置 WORKER_SANDBOX_COMMAND；生产执行链禁止退化为同 UID 的普通子进程"
            )
        try:
            sandbox_command_list = shlex.split(sandbox_command_str)
        except ValueError as exc:
            raise RuntimeError("WORKER_SANDBOX_COMMAND 不是合法参数列表") from exc
        if not sandbox_command_list:
            raise RuntimeError(f"沙箱工具不可用: {sandbox_command_str}")
        # P0-01: 启动时 PATH 解析绝对路径固化到 SandboxConfig，阻断 exec_plan.env 注入 PATH 走伪造 bwrap（wrap_command 再断言绝对路径）。
        resolved_sandbox_bin = shutil.which(sandbox_command_list[0])
        if resolved_sandbox_bin is None:
            raise RuntimeError(f"沙箱工具不可用: {sandbox_command_list[0]}")
        sandbox_command_list[0] = os.path.abspath(resolved_sandbox_bin)

        sandbox_config = SandboxConfig(
            enabled=True,
            network_isolated=bool(getattr(config, "sandbox_network_isolated", False)),
            fs_isolated=True,
            sandbox_command=sandbox_command_list,
        )
        logger.info(
            "P0-a5: 使用 SandboxExecutor (sandbox_mode=sandbox, sandbox_command={}, network_isolated={})",
            sandbox_command_list,
            sandbox_config.network_isolated,
        )
        return SandboxExecutor(config=exec_config, sandbox_config=sandbox_config)

    if sandbox_mode == "process":
        raise RuntimeError("WORKER_SANDBOX_MODE=process 已禁用：用户任务必须运行在真实隔离环境中")
    raise RuntimeError(f"未知的 WORKER_SANDBOX_MODE: {sandbox_mode!r}")


def _create_plugin_registry(config: Any) -> Any:
    """创建插件注册表"""
    from antcode_worker.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.load_builtin_plugins()
    return registry


def _create_log_manager(config: Any, transport: Any) -> Any:
    """创建日志管理器"""
    from antcode_worker.logs.manager import LogManagerConfig, LogManagerFactory

    log_config = LogManagerConfig()

    return LogManagerFactory(transport=transport, config=log_config)


def _create_metrics_collector(config: Any) -> Any:
    """创建系统指标采集器"""
    from antcode_worker.heartbeat.system_metrics import init_metrics_collector

    max_concurrent = getattr(config, "max_concurrent_tasks", 5)
    return init_metrics_collector(max_slots=max_concurrent)


def _create_heartbeat_reporter(config: Any, transport: Any, metrics_collector: Any) -> Any:
    """创建心跳上报器"""
    from antcode_worker.heartbeat.reporter import HeartbeatReporter

    worker_id = "unknown"
    gateway_config = getattr(transport, "_gateway_config", None)
    if gateway_config and getattr(gateway_config, "worker_id", None):
        worker_id = gateway_config.worker_id
    else:
        transport_worker_id = getattr(transport, "_worker_id", None)
        if transport_worker_id:
            worker_id = transport_worker_id
    version = getattr(config, "version", "0.1.0")
    max_concurrent = getattr(config, "max_concurrent_tasks", 5)
    name = getattr(config, "name", "")
    host = getattr(config, "host", "")
    port = getattr(config, "port", 8001)
    region = getattr(config, "region", "")
    if host in ("", "0.0.0.0", "127.0.0.1", "localhost"):
        from antcode_worker.config import get_local_ip

        host = get_local_ip()

    return HeartbeatReporter(
        transport=transport,
        worker_id=worker_id,
        metrics_collector=metrics_collector,
        version=version,
        max_concurrent_tasks=max_concurrent,
        name=name,
        host=host,
        port=port,
        region=region,
    )


def _create_tombstone_redis(config: Any) -> Any:
    # P1-SM-03 (round6): async Redis for CancelTombstones; None = 降级纯内存
    redis_url = getattr(config, "redis_url", "")
    if not redis_url:
        return None
    try:
        from antcode_core.infrastructure.redis.factory import create_async_redis_client

        return create_async_redis_client(redis_url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 - 备份可选,不阻塞 Worker
        logger.warning("cancel tombstones Redis client 创建失败,降级纯内存: {}", exc)
        return None


def _create_engine(
    config: Any,
    transport: Any,
    runtime_manager: Any,
    executor: Any,
    plugin_registry: Any,
    log_manager: Any,
    project_fetcher: Any,
    artifact_manager: Any,
    *,
    tombstone_redis: Any = None,
) -> Any:
    """创建引擎"""
    from antcode_worker.engine.engine import Engine

    max_concurrent = getattr(config, "max_concurrent_tasks", 5)
    flow_controller = _create_flow_controller(config)

    engine = Engine(
        transport=transport,
        executor=executor,
        flow_controller=flow_controller,
        runtime_manager=runtime_manager,
        plugin_registry=plugin_registry,
        log_manager_factory=log_manager,
        project_fetcher=project_fetcher,
        artifact_manager=artifact_manager,
        max_concurrent=max_concurrent,
        memory_limit_mb=getattr(config, "task_memory_limit_mb", 0),
        cpu_limit_seconds=getattr(config, "task_cpu_time_limit_sec", 0),
        tombstone_redis=tombstone_redis,
        tombstone_namespace=getattr(config, "redis_namespace", "antcode"),
    )

    # 绑定指标采集器
    from antcode_worker.heartbeat.system_metrics import get_metrics_collector

    collector = get_metrics_collector()
    if collector:
        collector.set_state_manager(engine.state_manager)
        collector.set_scheduler(engine.scheduler)

    return engine


def _create_flow_controller(config: Any) -> Any:
    """创建流控控制器"""
    from antcode_worker.transport.flow_control import (
        FlowControlConfig,
        FlowControlStrategy,
        create_flow_controller,
    )

    enabled = getattr(config, "flow_control_enabled", False)
    if not enabled:
        return None

    strategy_value = getattr(config, "flow_control_strategy", "token_bucket")
    strategy = FlowControlStrategy(strategy_value)

    flow_config = FlowControlConfig(
        strategy=strategy,
        bucket_capacity=getattr(config, "flow_control_capacity", 200),
        refill_rate=getattr(config, "flow_control_rate", 100.0),
    )
    return create_flow_controller(strategy, flow_config)


def _create_artifact_transfer_store(config: Any, transport: Any) -> Any:
    """Select the artifact data plane that matches the Worker transport."""
    mode = str(getattr(config, "transport_mode", "gateway") or "gateway").lower()
    if mode == "direct":
        from antcode_worker.artifact_transfer import PostgresArtifactTransferStore

        return PostgresArtifactTransferStore()
    if mode == "gateway":
        from antcode_worker.transport.gateway.artifacts import (
            GatewayArtifactTransferStore,
        )

        return GatewayArtifactTransferStore(transport)
    raise RuntimeError(f"未知的 Worker transport_mode: {mode!r}")


def _create_project_fetcher(config: Any, transport: Any) -> Any:
    """创建项目获取器"""
    import os

    from antcode_worker.config import DATA_ROOT
    from antcode_worker.projects.fetcher import ArtifactFetcher, ProjectWorkspace

    data_dir = getattr(config, "data_dir", str(DATA_ROOT))
    runs_dir = getattr(config, "runs_dir", None) or os.path.join(data_dir, "runs")
    workspace = ProjectWorkspace(root_dir=os.path.join(runs_dir, "sources"))
    store = _create_artifact_transfer_store(config, transport)
    return ArtifactFetcher(workspace=workspace, artifact_store=store)


def _create_artifact_manager(config: Any, transport: Any) -> Any:
    """创建产物管理器"""
    from antcode_worker.executor.artifacts import ArtifactManager

    store = _create_artifact_transfer_store(config, transport)
    return ArtifactManager(artifact_store=store)


def _create_observability_server(config: Any, transport: Any, engine: Any) -> Any:
    """创建可观测性服务器"""
    from antcode_worker.observability.health import HealthResult, HealthStatus
    from antcode_worker.observability.server import ObservabilityServer

    server = ObservabilityServer()

    def transport_check():
        if transport.is_connected:
            return HealthResult(status=HealthStatus.HEALTHY, message="transport ok")
        return HealthResult(status=HealthStatus.UNHEALTHY, message="transport offline")

    def slots_check():
        stats = engine.get_stats()
        if not stats.get("running", False):
            return HealthResult(status=HealthStatus.UNHEALTHY, message="engine stopped")
        if stats.get("queue_size", 0) >= stats.get("max_concurrent", 1) * 2:
            return HealthResult(status=HealthStatus.DEGRADED, message="queue full")
        return HealthResult(status=HealthStatus.HEALTHY, message="slots ok")

    server.register_health_check("transport", transport_check)
    server.register_health_check("slots", slots_check)

    return server
