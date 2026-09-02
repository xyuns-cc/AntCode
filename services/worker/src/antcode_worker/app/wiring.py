"""依赖注入容器：组装 Worker 的所有组件。Requirements: 2.4"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from loguru import logger

from antcode_worker.app.control_plane_rejection import require_success_body


@dataclass
class Container:
    """管理 Worker 所有组件的生命周期和依赖关系。"""

    config: Any = None

    transport: Any = None
    engine: Any = None
    runtime_manager: Any = None
    executor: Any = None
    plugin_registry: Any = None
    log_manager: Any = None
    heartbeat_reporter: Any = None

    metrics_server: Any = None
    health_server: Any = None
    observability_server: Any = None
    metrics_collector: Any = None

    project_fetcher: Any = None
    artifact_manager: Any = None

    identity: Any = None
    secrets: Any = None

    _initialized: bool = False
    _components: dict[str, Any] = field(default_factory=dict)

    def register(self, name: str, component: Any) -> None:
        self._components[name] = component
        setattr(self, name, component)
        logger.debug(f"组件已注册: {name}")

    def get(self, name: str) -> Any | None:
        return self._components.get(name) or getattr(self, name, None)

    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
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
    """创建并配置依赖容器"""
    container = Container(config=config)

    # T6-T4a: registry 提前创建，否则 transport 内 register-direct 时读不到能力
    plugin_registry = _create_plugin_registry(config)
    container.register("plugin_registry", plugin_registry)
    from antcode_worker.app.capabilities import detect_plugin_capabilities

    capabilities = detect_plugin_capabilities(plugin_registry)

    # 两种模式都必须注入启动能力快照，否则控制面拿到空能力、Lease 快照与真实
    # 插件集不一致（Direct 侧续租被判 capabilities_changed）。
    transport = _create_transport(config)
    transport.set_capabilities(capabilities)
    container.register("transport", transport)

    runtime_manager = _create_runtime_manager(config)
    container.register("runtime_manager", runtime_manager)

    executor = _create_executor(config)
    container.register("executor", executor)

    log_manager = _create_log_manager(config, transport)
    container.register("log_manager", log_manager)

    metrics_collector = _create_metrics_collector(config)
    container.register("metrics_collector", metrics_collector)

    heartbeat_reporter = _create_heartbeat_reporter(config, transport, metrics_collector)
    container.register("heartbeat_reporter", heartbeat_reporter)

    project_fetcher = _create_project_fetcher(config, transport)
    container.register("project_fetcher", project_fetcher)
    artifact_manager = _create_artifact_manager(config, transport)
    container.register("artifact_manager", artifact_manager)

    from antcode_worker.app.engine_wiring import create_engine

    engine = create_engine(
        config=config,
        transport=transport,
        runtime_manager=runtime_manager,
        executor=executor,
        plugin_registry=plugin_registry,
        log_manager=log_manager,
        project_fetcher=project_fetcher,
        artifact_manager=artifact_manager,
        metrics_collector=metrics_collector,
        heartbeat_reporter=heartbeat_reporter,
        tombstone_redis=_create_tombstone_redis(transport),
    )
    container.register("engine", engine)

    observability_server = _create_observability_server(transport, engine, metrics_collector)
    container.register("observability_server", observability_server)

    container.mark_initialized()
    logger.info("依赖容器初始化完成")

    return container


def _create_transport(config: Any) -> Any:
    """创建传输层"""
    from antcode_worker.transport.factory import (
        DirectConfig,
        GatewayConfigSpec,
        TransportConfig,
        build_direct_control_client,
        build_gateway_transport_config,
    )

    transport_mode = getattr(config, "transport_mode", "gateway")

    bootstrap = _prepare_transport_bootstrap(config, transport_mode)
    worker_id = bootstrap.worker_id
    credentials = bootstrap.credentials
    if transport_mode == "direct":
        worker_id, credentials = _prepare_direct_transport(config, bootstrap)

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
                secret_key=str(getattr(credentials, "secret_key", "") or ""),
            )
            if transport_mode == "gateway"
            else GatewayConfigSpec()
        ),
    )

    # 启动自检需异步，放到 lifecycle 执行，这里只做同步校验 + Banner。
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

    if transport_mode == "direct":
        from antcode_worker.transport.redis import RedisTransport

        return RedisTransport(
            redis_url=transport_config.direct.redis_url,
            worker_id=worker_id,
            namespace=transport_config.direct.redis_namespace,
            consumer_group=transport_config.direct.consumer_group,
            direct_control=build_direct_control_client(transport_config.direct, worker_id or ""),
            task_payload_secret=transport_config.direct.secret_key,
        )
    else:
        from antcode_worker.transport.gateway import GatewayTransport

        # P2-20: worker_id 必须在构造时写入——deregister() 读的是它；set_credentials 只是兜底
        gateway_config = build_gateway_transport_config(transport_config)
        transport = GatewayTransport(gateway_config=gateway_config)
        if worker_id:
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


def _require_control_credentials(config: Any, credential_service: Any, credentials: Any, *, required: bool) -> Any:
    """本地有一份结构合法的凭据就直接放行，不向控制面求证。

    这是**刻意**的：控制面拒绝这份身份时（例如库被重建后 worker_id 已不存在），
    正确动作是带可执行指令硬失败，而不是自动拿安装 Key 重注册——安装 Key 一次性
    （ACK 后恢复窗口永久关闭，库重建后 Key 记录本身也没了），且"被拒就自己回来"
    会打穿管理员删除/停用 Worker 这条撤销手段。

    那句可执行报错由谁给取决于传输模式：Direct 在 ACL 签发那步、Gateway 在残留的
    注册 ACK 那步，由 ``control_plane_rejection`` 给出。但**稳态 Gateway Worker 一次
    签名 HTTP 请求都不发**（租约/心跳/派发全走 gRPC），库重建后它先撞上 Gateway 拦截器
    的 ``UNAUTHENTICATED 无效的 API Key``，最终以 ``RuntimeError("传输层启动失败")``
    退出——该链路无结构化归因，见 ``control_plane_rejection`` 模块文档的适用范围一段。
    """
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


def _register_by_install_key(
    config: Any,
    credential_service: Any,
):
    from antcode_worker.app.worker_registration import register_by_install_key

    return register_by_install_key(config, credential_service)


def _issue_direct_redis_acl(*, config: Any, credentials: Any | None, credential_service: Any):
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
    from antcode_core.common.utils.worker_request import (
        HTTP_POST_METHOD,
        build_worker_signed_headers,
        encode_worker_json_body,
        request_path_from_url,
    )

    from antcode_worker.app.http_trust import certificate_authority, should_trust_env_proxy

    api_base_url = _normalize_api_base_url(
        getattr(config, "api_base_url", ""),
        getattr(config, "gateway_host", "localhost"),
        allow_insecure_internal=getattr(config, "api_allow_insecure_internal", False),
    )
    url = f"{api_base_url}/api/v1/workers/{credentials.worker_id}/redis-acl/issue"
    request_body = encode_worker_json_body({})
    headers = build_worker_signed_headers(
        SimpleNamespace(public_id=credentials.worker_id),
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        method=HTTP_POST_METHOD,
        path=request_path_from_url(url),
        body=request_body,
    )
    client = httpx.Client(
        timeout=15.0,
        trust_env=should_trust_env_proxy(api_base_url),
        verify=certificate_authority(),
    )
    with client:
        response = client.post(url, content=request_body, headers=headers)
    response_body = require_success_body(
        response, operation="Direct Redis ACL 签发", credentials_at=credential_service.store.describe_location()
    )
    data = response_body.get("data") or {}
    username = str(data.get("redis_username") or "")
    password = str(data.get("redis_password") or "")
    if not username or not password:
        raise RuntimeError("Direct Redis ACL 签发返回数据不完整")
    updated = replace(credentials, redis_username=username, redis_password=password)
    if not credential_service.save(updated):
        raise RuntimeError("Direct Redis ACL 已轮换，但新凭据持久化失败")
    return updated


def _create_runtime_manager(config: Any) -> Any:
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
    """按 config.sandbox_mode 分派执行器；sandbox 以外的模式一律拒绝启动。"""
    import os
    import shlex
    import shutil

    from antcode_worker.executor.base import ExecutorConfig
    from antcode_worker.executor.sandbox import SandboxConfig, SandboxExecutor
    from antcode_worker.rule_egress_limits import limits_from_config

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
        rule_egress_limits=limits_from_config(config),
    )

    sandbox_mode = str(getattr(config, "sandbox_mode", "sandbox") or "sandbox").strip().lower()
    if sandbox_mode == "sandbox":
        network_isolated = bool(getattr(config, "sandbox_network_isolated", True))
        if not network_isolated:
            raise RuntimeError("WORKER_SANDBOX_NETWORK_ISOLATED=false 已禁用：用户任务必须使用独立 network namespace")
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

        sandbox_config = SandboxConfig.for_worker(
            config,
            network_isolated=network_isolated,
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
    from antcode_worker.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.load_builtin_plugins()
    return registry


def _create_log_manager(config: Any, transport: Any) -> Any:
    from antcode_worker.logs.manager import LogManagerConfig, LogManagerFactory

    log_config = LogManagerConfig()

    return LogManagerFactory(transport=transport, config=log_config)


def _create_metrics_collector(config: Any) -> Any:
    from antcode_worker.heartbeat.system_metrics import init_metrics_collector

    max_concurrent = getattr(config, "max_concurrent_tasks", 5)
    return init_metrics_collector(max_slots=max_concurrent)


def _create_heartbeat_reporter(config: Any, transport: Any, metrics_collector: Any) -> Any:
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


def _create_tombstone_redis(transport: Any) -> Any:
    """CancelTombstones 的 Redis 客户端，复用 transport 已认证的连接串。

    不能用 ``config.redis_url``：那不含运行时签发的 per-worker ACL 凭据，查询必然
    "Authentication required"，取消墓碑会退化成单进程内存、跨进程取消语义静默失效。
    Gateway 模式无连接串返回 None 属设计内。"""
    redis_url = str(getattr(transport, "_redis_url", "") or "")
    if not redis_url:
        return None
    from antcode_core.infrastructure.redis.factory import create_async_redis_client

    return create_async_redis_client(redis_url, decode_responses=True)


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
    import os

    from antcode_worker.config import DATA_ROOT
    from antcode_worker.projects.fetcher import ArtifactFetcher, ProjectWorkspace

    data_dir = getattr(config, "data_dir", str(DATA_ROOT))
    runs_dir = getattr(config, "runs_dir", None) or os.path.join(data_dir, "runs")
    workspace = ProjectWorkspace(root_dir=os.path.join(runs_dir, "sources"))
    store = _create_artifact_transfer_store(config, transport)
    return ArtifactFetcher(workspace=workspace, artifact_store=store)


def _create_artifact_manager(config: Any, transport: Any) -> Any:
    from antcode_worker.executor.artifacts import ArtifactManager

    store = _create_artifact_transfer_store(config, transport)
    return ArtifactManager(artifact_store=store)


def _create_observability_server(transport: Any, engine: Any, metrics_collector: Any) -> Any:
    """创建可观测性服务器；``metrics_collector`` 就是心跳那一份，/metrics 与资源页必须同源。"""
    from antcode_worker.observability.health import HealthResult, HealthStatus
    from antcode_worker.observability.server import ObservabilityServer

    server = ObservabilityServer(metrics_collector)

    def transport_check():
        if transport.is_connected:
            return HealthResult(status=HealthStatus.HEALTHY, message="transport ok")
        return HealthResult(status=HealthStatus.UNHEALTHY, message="transport offline")

    def engine_check():
        # 只有"重启才能修"的故障才配进存活探针：引擎停了、传输被永久 halt（lease 撤销/
        # 认证中止）。断线重连是无限重试的暂态、队列满是背压，都不该重启（见回归测试）。
        stats = engine.get_stats()
        if stats.get("running", False) and transport.is_running:
            return HealthResult(status=HealthStatus.HEALTHY, message="engine ok")
        return HealthResult(status=HealthStatus.UNHEALTHY, message="engine or transport halted")

    server.register_health_check("transport", transport_check)
    server.register_health_check("engine", engine_check, liveness=True)

    return server
