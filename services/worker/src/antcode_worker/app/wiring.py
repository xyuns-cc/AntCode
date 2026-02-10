"""
依赖注入容器

负责组装 Worker 的所有组件。

Requirements: 2.4
"""

from dataclasses import dataclass, field
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
    log_cleanup: Any = None
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


def create_container(config: Any) -> Container:
    """
    创建并配置依赖容器

    Args:
        config: Worker 配置

    Returns:
        配置好的容器
    """
    container = Container(config=config)

    # 1. 创建传输层
    transport = _create_transport(config)
    container.register("transport", transport)

    # 2. 创建运行时管理器
    runtime_manager = _create_runtime_manager(config)
    container.register("runtime_manager", runtime_manager)

    # 3. 创建执行器
    executor = _create_executor(config)
    container.register("executor", executor)

    # 4. 创建插件注册表
    plugin_registry = _create_plugin_registry(config)
    container.register("plugin_registry", plugin_registry)

    # 5. 创建日志管理器工厂
    log_manager = _create_log_manager(config, transport)
    container.register("log_manager", log_manager)

    # 6. 创建日志清理服务
    log_cleanup = _create_log_cleanup_service(config)
    container.register("log_cleanup", log_cleanup)

    # 7. 创建指标采集器
    metrics_collector = _create_metrics_collector(config)
    container.register("metrics_collector", metrics_collector)

    # 8. 创建心跳上报器
    heartbeat_reporter = _create_heartbeat_reporter(
        config, transport, metrics_collector
    )
    container.register("heartbeat_reporter", heartbeat_reporter)

    # 9. 创建项目获取器与产物管理器
    project_fetcher = _create_project_fetcher(config)
    container.register("project_fetcher", project_fetcher)
    artifact_manager = _create_artifact_manager(config)
    container.register("artifact_manager", artifact_manager)

    # 10. 创建引擎（依赖其他组件）
    engine = _create_engine(
        config=config,
        transport=transport,
        runtime_manager=runtime_manager,
        executor=executor,
        plugin_registry=plugin_registry,
        log_manager=log_manager,
        project_fetcher=project_fetcher,
        artifact_manager=artifact_manager,
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
    from antcode_worker.services.credential import (
        get_credential_store,
        init_credential_service,
    )
    from antcode_worker.transport.factory import (
        DirectConfig,
        GatewayConfigSpec,
        TransportConfig,
    )

    # 构建传输层配置
    transport_mode = getattr(config, "transport_mode", "gateway")

    # 加载凭证
    credential_store = getattr(config, "credential_store", "file")
    credential_service = init_credential_service(get_credential_store(credential_store))
    credentials = credential_service.load()

    # Gateway 模式：若无有效凭证，必须使用安装 Key 注册
    if transport_mode == "gateway" and (not credentials or not credentials.is_valid()):
        credentials = _register_by_install_key(
            config=config,
            credential_service=credential_service,
        )
        if not credentials or not credentials.is_valid():
            from antcode_worker.transport.factory import TransportConfigError

            message = (
                "Gateway 模式首次启动必须配置安装 Key\n"
                "示例: ANTCODE_WORKER_KEY=xxx 或 WORKER_KEY=xxx"
            )
            logger.error(f"传输层配置错误: {message}")
            raise TransportConfigError(message)
    worker_id = None

    # 从环境变量读取 worker_id（优先级高于配置与凭证）
    import os
    env_worker_id = os.getenv("WORKER_ID") or os.getenv("ANTCODE_WORKER_ID")
    if env_worker_id:
        worker_id = env_worker_id

    # 从凭证覆盖配置
    gateway_host = getattr(config, "gateway_host", "localhost")
    gateway_port = getattr(config, "gateway_port", 50051)
    api_key = getattr(config, "api_key", None)

    if not api_key:
        api_key = os.getenv("WORKER_API_KEY") or os.getenv("ANTCODE_API_KEY")

    if credentials and credentials.is_valid():
        if credentials.gateway_host:
            gateway_host = credentials.gateway_host
        if credentials.gateway_port:
            gateway_port = credentials.gateway_port
        # 环境变量优先级高于凭证文件
        if not env_worker_id and credentials.worker_id:
            worker_id = credentials.worker_id
        if not api_key and credentials.api_key:
            api_key = credentials.api_key

    if not worker_id and transport_mode == "direct":
        from pathlib import Path

        from antcode_worker.security import init_identity_manager
        from antcode_worker.config import DATA_ROOT

        data_dir = getattr(config, "data_dir", str(DATA_ROOT))
        identity_path = Path(data_dir) / "identity" / "worker_identity.yaml"
        name = getattr(config, "name", "")
        labels = {"name": name} if name else None
        identity_manager = init_identity_manager(
            identity_path=identity_path,
            zone=getattr(config, "region", "default"),
            labels=labels,
            version=getattr(config, "version", "0.1.0"),
            install_signal_handler=False,
        )
        worker_id = identity_manager.worker_id
        logger.info("Direct 模式未配置 worker_id，已生成本地身份: {}", worker_id)

    if transport_mode == "direct":
        _register_direct_worker(config=config, worker_id=worker_id)

    # 构建配置对象
    transport_config = TransportConfig(
        mode=transport_mode,
        worker_id=worker_id,
        direct=DirectConfig(
            redis_url=getattr(config, "redis_url", ""),
            redis_namespace=getattr(config, "redis_namespace", "antcode"),
        ),
        gateway=GatewayConfigSpec(
            host=gateway_host,
            port=gateway_port,
            tls=getattr(config, "gateway_tls", False),
            ca_cert=getattr(config, "ca_cert", None),
            client_cert=getattr(config, "client_cert", None),
            client_key=getattr(config, "client_key", None),
            api_key=api_key,
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
        )
    else:
        from antcode_worker.transport.gateway import GatewayConfig, GatewayTransport
        gateway_config = GatewayConfig(
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            use_tls=transport_config.gateway.tls,
            ca_cert_path=transport_config.gateway.ca_cert,
            client_cert_path=transport_config.gateway.client_cert,
            client_key_path=transport_config.gateway.client_key,
            api_key=api_key,
            worker_id=worker_id,
        )
        transport = GatewayTransport(gateway_config=gateway_config)
        if worker_id:
            transport.set_credentials(worker_id=worker_id)
        return transport


def _normalize_api_base_url(value: str | None, gateway_host: str) -> str:
    url = (value or "").strip()
    if not url:
        url = f"http://{gateway_host}:8000"
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.rstrip("/")


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


def _mask_redis_url(redis_url: str) -> str:
    if "@" not in redis_url:
        return redis_url
    prefix, suffix = redis_url.split("@", 1)
    if ":" in prefix:
        prefix = prefix.rsplit(":", 1)[0] + ":***"
    return f"{prefix}@{suffix}"


def _register_by_install_key(
    config: Any,
    credential_service: Any,
):
    """使用安装 Key 注册 Worker，返回凭证（失败抛错）"""
    import os
    import secrets
    import time

    worker_key = getattr(config, "worker_key", "") or os.getenv("ANTCODE_WORKER_KEY") or os.getenv("WORKER_KEY")
    if not worker_key:
        return None

    gateway_host = getattr(config, "gateway_host", "localhost")
    gateway_port = getattr(config, "gateway_port", 50051)
    api_base_url = _normalize_api_base_url(
        getattr(config, "api_base_url", "") or os.getenv("WORKER_API_BASE_URL") or os.getenv("ANTCODE_API_BASE_URL") or os.getenv("API_BASE_URL"),
        gateway_host,
    )

    host = getattr(config, "host", "")
    if host in ("", "0.0.0.0", "127.0.0.1", "localhost"):
        from antcode_worker.config import get_local_ip
        host = get_local_ip()

    payload = {
        "key": worker_key,
        "name": getattr(config, "name", "Worker-001"),
        "host": host,
        "port": getattr(config, "port", 8001),
        "region": getattr(config, "region", ""),
        "client_timestamp": int(time.time()),
        "client_nonce": secrets.token_hex(16),
    }

    url = f"{api_base_url}/api/v1/workers/register-by-key"
    logger.info("检测到安装 Key，开始注册 Worker: {}", url)

    try:
        import httpx

        trust_env = _should_trust_env_proxy(api_base_url)
        with httpx.Client(timeout=15.0, trust_env=trust_env) as client:
            response = client.post(url, json=payload)
    except Exception as e:
        logger.error("安装 Key 注册请求失败: {}", e)
        raise

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.status_code >= 400:
        detail = None
        if isinstance(body, dict):
            detail = body.get("message") or body.get("detail")
        raise RuntimeError(detail or f"安装 Key 注册失败 (HTTP {response.status_code})")

    if not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(message or "安装 Key 注册失败")

    data = body.get("data") or {}
    worker_id = data.get("worker_id", "")
    api_key = data.get("api_key", "")
    secret_key = data.get("secret_key", "")

    if not worker_id or not api_key or not secret_key:
        raise RuntimeError("安装 Key 注册返回数据不完整")

    from antcode_worker.services.credential import WorkerCredentials

    credentials = WorkerCredentials(
        worker_id=worker_id,
        api_key=api_key,
        secret_key=secret_key,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
    )

    saved = credential_service.save(credentials)
    if not saved:
        logger.warning("安装 Key 注册成功，但保存凭证失败")
    else:
        logger.info("安装 Key 注册成功: worker_id={}", worker_id)

    return credentials


def _register_direct_worker(
    config: Any,
    worker_id: str | None,
):
    """Direct 模式注册 Worker 到控制平面（失败抛错）"""
    import os
    import platform
    import secrets

    from antcode_worker.transport.factory import TransportConfigError
    from antcode_worker.heartbeat.reporter import get_capability_detector

    if not worker_id:
        raise TransportConfigError("Direct 模式必须配置 worker_id")

    gateway_host = getattr(config, "gateway_host", "localhost")
    api_base_url = _normalize_api_base_url(
        getattr(config, "api_base_url", "")
        or os.getenv("WORKER_API_BASE_URL")
        or os.getenv("ANTCODE_API_BASE_URL")
        or os.getenv("API_BASE_URL"),
        gateway_host,
    )

    host = getattr(config, "host", "")
    if host in ("", "0.0.0.0", "127.0.0.1", "localhost"):
        from antcode_worker.config import get_local_ip

        host = get_local_ip()

    redis_url = getattr(config, "redis_url", "")
    if not redis_url:
        raise TransportConfigError("Direct 模式必须配置 redis_url")

    from antcode_core.infrastructure.redis import direct_register_proof_key

    proof = secrets.token_hex(16)
    proof_key = direct_register_proof_key(
        worker_id,
        namespace=getattr(config, "redis_namespace", None),
    )
    try:
        import redis

        redis_client = redis.Redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        redis_client.set(proof_key, proof, ex=60)
        logger.info(
            "Direct 注册证明已写入 Redis: {} ({})",
            _mask_redis_url(redis_url),
            proof_key,
        )
    except Exception as e:
        raise TransportConfigError(f"写入 Direct 注册证明失败: {e}") from e

    payload = {
        "worker_id": worker_id,
        "proof": proof,
        "name": getattr(config, "name", "") or worker_id,
        "host": host,
        "port": getattr(config, "port", 8001),
        "region": getattr(config, "region", ""),
        "version": getattr(config, "version", ""),
        "os_type": platform.system(),
        "os_version": platform.release(),
        "python_version": platform.python_version(),
        "machine_arch": platform.machine(),
        "capabilities": get_capability_detector().detect_all(),
    }

    url = f"{api_base_url}/api/v1/workers/register-direct"
    logger.info("Direct Worker 注册: {}", url)

    try:
        import httpx

        trust_env = _should_trust_env_proxy(api_base_url)
        with httpx.Client(timeout=10.0, trust_env=trust_env) as client:
            response = client.post(url, json=payload)
    except Exception as e:
        logger.error("Direct Worker 注册请求失败: {}", e)
        raise

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.status_code >= 400:
        detail = None
        if isinstance(body, dict):
            detail = body.get("message") or body.get("detail")
        if detail == "无效的 Direct 注册证明":
            detail = (
                "无效的 Direct 注册证明"
                "（请确认 Web API 的 REDIS_URL 与 Worker 的 redis_url 指向同一 Redis）"
            )
        raise RuntimeError(detail or f"Direct Worker 注册失败 (HTTP {response.status_code})")

    if not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(message or "Direct Worker 注册失败")

    data = body.get("data") or {}
    created = data.get("created")
    if created is True:
        logger.info("Direct Worker 注册成功: worker_id={}", worker_id)
    else:
        logger.info("Direct Worker 已注册: worker_id={}", worker_id)


def _create_runtime_manager(config: Any) -> Any:
    """创建运行时管理器"""
    import os

    from antcode_worker.runtime.manager import RuntimeManager, RuntimeManagerConfig
    from antcode_worker.runtime.uv_manager import uv_manager
    from antcode_worker.config import DATA_ROOT

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
    """创建执行器"""
    from antcode_worker.executor import ExecutorConfig, ProcessExecutor

    max_concurrent = getattr(config, "max_concurrent_tasks", 5)
    default_timeout = getattr(config, "task_timeout", 3600)
    cpu_limit = getattr(config, "task_cpu_time_limit_sec", 0)
    memory_limit = getattr(config, "task_memory_limit_mb", 0)

    exec_config = ExecutorConfig(
        max_concurrent=max_concurrent,
        default_timeout=default_timeout,
        default_cpu_limit_seconds=cpu_limit if cpu_limit > 0 else 0,
        default_memory_limit_mb=memory_limit if memory_limit > 0 else 0,
    )
    return ProcessExecutor(exec_config)


def _create_plugin_registry(config: Any) -> Any:
    """创建插件注册表"""
    from antcode_worker.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.load_builtin_plugins()
    return registry


def _create_log_manager(config: Any, transport: Any) -> Any:
    """创建日志管理器"""
    import os

    from antcode_worker.logs.archive import ArchiveConfig
    from antcode_worker.logs.manager import LogManagerConfig, LogManagerFactory
    from antcode_worker.logs.spool import SpoolConfig
    from antcode_worker.config import DATA_ROOT

    data_dir = getattr(config, "data_dir", str(DATA_ROOT))
    logs_dir = getattr(config, "logs_dir", None) or os.path.join(data_dir, "logs")

    # WAL 目录用于高可靠归档
    wal_dir = getattr(config, "wal_dir", None) or os.path.join(logs_dir, "wal")
    spool_dir = getattr(config, "spool_dir", None) or os.path.join(logs_dir, "spool")

    log_config = LogManagerConfig(
        wal_dir=wal_dir,
        spool_config=SpoolConfig(spool_dir=spool_dir),
        archive_config=ArchiveConfig(wal_dir=wal_dir),
        enable_archive=True,
    )

    return LogManagerFactory(transport=transport, config=log_config)


def _create_log_cleanup_service(config: Any) -> Any:
    """创建日志清理服务"""
    from antcode_worker.services.log_cleanup import LogCleanupService
    from antcode_worker.config import DATA_ROOT
    import os

    if not getattr(config, "log_cleanup_enabled", True):
        return None

    data_dir = getattr(config, "data_dir", str(DATA_ROOT))
    logs_dir = getattr(config, "logs_dir", None) or os.path.join(data_dir, "logs")
    retention_days = getattr(config, "log_retention_days", 7)
    interval_hours = getattr(config, "log_cleanup_interval_hours", 24)
    return LogCleanupService(
        logs_dir=logs_dir,
        retention_days=retention_days,
        interval_hours=interval_hours,
    )


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


def _create_engine(
    config: Any,
    transport: Any,
    runtime_manager: Any,
    executor: Any,
    plugin_registry: Any,
    log_manager: Any,
    project_fetcher: Any,
    artifact_manager: Any,
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
    try:
        strategy = FlowControlStrategy(strategy_value)
    except ValueError:
        strategy = FlowControlStrategy.TOKEN_BUCKET

    flow_config = FlowControlConfig(
        strategy=strategy,
        bucket_capacity=getattr(config, "flow_control_capacity", 200),
        refill_rate=getattr(config, "flow_control_rate", 100.0),
    )
    return create_flow_controller(flow_config)


def _create_project_fetcher(config: Any) -> Any:
    """创建项目获取器"""
    import os

    from antcode_worker.projects.fetcher import ArtifactFetcher, ProjectCache
    from antcode_worker.config import DATA_ROOT

    data_dir = getattr(config, "data_dir", str(DATA_ROOT))
    cache_dir = getattr(config, "projects_dir", None) or os.path.join(data_dir, "projects")
    cache = ProjectCache(cache_dir=cache_dir)
    return ArtifactFetcher(cache=cache)


def _create_artifact_manager(config: Any) -> Any:
    """创建产物管理器"""
    import os

    from antcode_worker.executor.artifacts import ArtifactManager
    from antcode_worker.config import DATA_ROOT

    data_dir = getattr(config, "data_dir", str(DATA_ROOT))
    storage_dir = getattr(config, "runs_dir", None) or os.path.join(data_dir, "runs")
    return ArtifactManager(storage_dir=storage_dir)


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
