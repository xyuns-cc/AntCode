"""
工作Worker配置模块

提供Worker配置管理，支持同步和异步文件操作。

Requirements: 7.1
"""

import asyncio
import os
import socket
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from loguru import logger

try:
    import dotenv
except ImportError:  # pragma: no cover - optional dependency
    dotenv = None  # type: ignore[assignment]

# Worker根目录
WORKER_ROOT = Path(__file__).parent
SERVICE_ROOT = WORKER_ROOT.parent.parent  # services/worker/src -> services/worker
# 项目根目录（运行时数据统一放在 data/ 下）
PROJECT_ROOT = SERVICE_ROOT.parent.parent  # services/worker -> project root
PROJECT_DATA_ROOT = PROJECT_ROOT / "data"
DATA_ROOT = PROJECT_DATA_ROOT / "worker"

# Worker配置文件路径
WORKER_CONFIG_FILE = DATA_ROOT / "worker_config.yaml"

_ENV_LOADED = False
_PRIVATE_CONFIG_MODE = 0o600


def _atomic_write_private_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, _PRIVATE_CONFIG_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        _set_private_config_permissions(temp_path)
        os.replace(temp_path, path)
        _set_private_config_permissions(path)
        _fsync_config_directory(path.parent)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _fsync_config_directory(path: Path) -> None:
    if os.name == "nt":
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _set_private_config_permissions(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, _PRIVATE_CONFIG_MODE)
        return
    from antcode_worker.services.credential.private_permissions import set_owner_only_permissions

    set_owner_only_permissions(path, _PRIVATE_CONFIG_MODE)


def _load_env_file() -> None:
    """加载 .env 环境变量（仅一次）"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    if dotenv is None:
        return

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        dotenv.load_dotenv(env_path, override=False)


def _get_env_value(*keys: str) -> str | None:
    """按优先顺序读取环境变量"""
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    return None


def _get_env_int(*keys: str) -> int | None:
    value = _get_env_value(*keys)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _get_env_bool(*keys: str) -> bool | None:
    value = _get_env_value(*keys)
    if value is None:
        return None
    return value.lower() in ("1", "true", "yes", "on")


def _normalize_path(path_value: str) -> str:
    """将路径标准化为绝对路径（相对路径基于项目根目录）"""
    expanded = os.path.expandvars(os.path.expanduser(str(path_value))).strip()
    if not expanded:
        return expanded

    path = Path(expanded)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve(strict=False)
    data_root = PROJECT_DATA_ROOT.resolve(strict=False)
    if os.path.commonpath([str(data_root), str(resolved)]) != str(data_root):
        raise ValueError("运行时路径必须位于项目根 data 目录下")
    return str(resolved)


def _load_env_config() -> dict[str, Any]:
    """读取环境变量配置"""
    env_config: dict[str, Any] = {}
    _load_control_api_env(env_config)
    _load_transport_env(env_config)
    _load_sandbox_env(env_config)
    _load_identity_env(env_config)
    _load_capacity_env(env_config)
    return env_config


def _load_transport_env(env_config: dict[str, Any]) -> None:
    _copy_env_value(env_config, "transport_mode", "WORKER_TRANSPORT_MODE")
    _copy_env_value(env_config, "redis_url", "WORKER_REDIS_URL")
    _copy_env_value(env_config, "redis_namespace", "WORKER_REDIS_NAMESPACE")
    env_config.update(_load_gateway_env())


def _load_sandbox_env(env_config: dict[str, Any]) -> None:
    _copy_env_value(env_config, "sandbox_mode", "WORKER_SANDBOX_MODE")
    _copy_env_value(env_config, "sandbox_command", "WORKER_SANDBOX_COMMAND")
    _copy_env_bool(env_config, "sandbox_network_isolated", "WORKER_SANDBOX_NETWORK_ISOLATED")


def _load_identity_env(env_config: dict[str, Any]) -> None:
    _copy_env_value(env_config, "name", "WORKER_NAME")
    _copy_env_value(env_config, "host", "WORKER_HOST")
    _copy_env_int(env_config, "port", "WORKER_PORT")
    _copy_env_value(env_config, "region", "WORKER_REGION")
    _copy_env_value(env_config, "data_dir", "WORKER_DATA_DIR")
    _copy_env_value(env_config, "credential_store", "WORKER_CREDENTIAL_STORE")
    _copy_env_value(env_config, "worker_key", "ANTCODE_WORKER_KEY")


def _load_capacity_env(env_config: dict[str, Any]) -> None:
    _copy_env_int(env_config, "heartbeat_interval", "WORKER_HEARTBEAT_INTERVAL")
    _copy_env_int(env_config, "max_concurrent_tasks", "WORKER_MAX_CONCURRENT_TASKS")
    from antcode_worker.rule_egress_limits import load_rule_egress_env

    env_config.update(load_rule_egress_env(_get_env_int))


def _copy_env_value(target: dict[str, Any], field: str, env_name: str) -> None:
    value = _get_env_value(env_name)
    if value:
        target[field] = value


def _copy_env_int(target: dict[str, Any], field: str, env_name: str) -> None:
    value = _get_env_int(env_name)
    if value is not None:
        target[field] = value


def _copy_env_bool(target: dict[str, Any], field: str, env_name: str) -> None:
    value = _get_env_bool(env_name)
    if value is not None:
        target[field] = value


def _load_gateway_env() -> dict[str, Any]:
    from antcode_worker.gateway_endpoint import (
        DEFAULT_GATEWAY_HOST,
        DEFAULT_GATEWAY_PORT,
        parse_gateway_address,
    )

    values: dict[str, Any] = {}
    endpoint = _get_env_value("WORKER_GATEWAY_ENDPOINT")
    host = _get_env_value("WORKER_GATEWAY_HOST")
    raw_port = _get_env_value("WORKER_GATEWAY_PORT")
    try:
        port = int(raw_port) if raw_port is not None else None
    except ValueError as exc:
        raise ValueError("WORKER_GATEWAY_PORT 必须是整数") from exc
    if endpoint or host or port is not None:
        address = parse_gateway_address(
            endpoint=endpoint or "",
            host=host or DEFAULT_GATEWAY_HOST,
            port=port if port is not None else DEFAULT_GATEWAY_PORT,
        )
        values.update(gateway_host=address.host, gateway_port=address.port)
    tls = _get_env_bool("WORKER_GATEWAY_TLS")
    if tls is not None:
        values["gateway_tls"] = tls
    for field_name, env_name in (
        ("ca_cert", "WORKER_CA_CERT"),
        ("client_cert", "WORKER_CLIENT_CERT"),
        ("client_key", "WORKER_CLIENT_KEY"),
    ):
        value = _get_env_value(env_name)
        if value:
            values[field_name] = value
    return values


def _load_control_api_env(env_config: dict[str, Any]) -> None:
    api_base_url = _get_env_value("WORKER_API_BASE_URL")
    if api_base_url:
        env_config["api_base_url"] = api_base_url
    allow_insecure = _get_env_bool("WORKER_API_ALLOW_INSECURE_INTERNAL")
    if allow_insecure is not None:
        env_config["api_allow_insecure_internal"] = allow_insecure


def get_local_ip() -> str:
    """获取本机 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@dataclass
class WorkerConfig:
    """
    Worker配置类

    Requirements: 7.1
    """

    # 基本配置
    name: str = "Worker-001"
    port: int = 8001  # 健康检查端口
    host: str = field(default_factory=get_local_ip)
    region: str = "默认"
    version: str = "0.1.0"
    description: str = ""
    tags: list[str] = field(default_factory=list)

    # 心跳配置
    heartbeat_interval: int = 30  # 心跳间隔（秒）
    heartbeat_timeout: int = 60  # 心跳超时时间（秒）

    # 任务配置
    max_concurrent_tasks: int = 0  # 最大并发任务数（0=自动计算）
    task_timeout: int = 3600  # 任务默认超时时间（秒）
    task_cpu_time_limit_sec: int = 0  # 单任务 CPU 时间上限（秒，0=自动）
    task_memory_limit_mb: int = 0  # 单任务可写内存上限（MB，0=自动；语义见 executor/process_limits.py）
    auto_resource_limit: bool = True  # 是否启用自适应资源限制

    # 沙箱硬限制（POSIX rlimit；非 POSIX 平台自动跳过）
    sandbox_enforce_rlimit: bool = True  # 默认强制开启 rlimit + 独立进程组
    # R1-P1-8 (审查报告)：V13 修复过 256 → 2048（executor/base.py:63-65 有注释）
    # 但 config 默认仍是 256，V13 在真实部署失效。scrapy 并发 + TLS + DNS
    # 稍多就会触发 EMFILE，此处同步改为 2048。
    sandbox_max_open_files: int = 2048  # RLIMIT_NOFILE
    sandbox_max_processes: int = 64  # RLIMIT_NPROC（防 fork 炸弹）

    # Rule 任务的 Worker 父进程出站代理硬预算。
    rule_egress_max_connections: int = 32
    rule_egress_max_bytes: int = 1024 * 1024 * 1024
    rule_egress_max_duration_seconds: int = 3600

    # 生产任务必须使用 SandboxExecutor。"process" 仅允许受信的本地开发，
    # 不提供租户级进程、网络或文件系统隔离。
    sandbox_mode: str = "sandbox"
    # 当前唯一受支持的启动器是 bwrap；wiring 会解析为绝对路径，执行器会拒绝
    # firejail、任意命令前缀和缺失启动器，避免配置漂移静默绕过隔离。
    sandbox_command: str = "bwrap"
    # bwrap 网络 namespace 隔离；Rule 任务只通过受限的 Worker 代理出站。
    sandbox_network_isolated: bool = True

    # 传输模式配置
    transport_mode: str = "gateway"  # 传输模式: "direct" 或 "gateway"

    # T6-T4a: RulePlugin 开关。默认开（当前主用途是爬虫）；code-only worker
    # 部署时可关掉减少 Scrapy 依赖启动开销。也可通过 env
    # `WORKER_ENABLE_RULE_PLUGIN=false` 覆盖。
    enable_rule_plugin: bool = True

    # Redis 配置（Direct 模式）
    redis_url: str = ""
    redis_namespace: str = "antcode"

    # Gateway 配置（Gateway 模式）
    gateway_host: str = "localhost"
    gateway_port: int = 50051
    gateway_tls: bool = False
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None

    # 控制平面 API 地址（用于安装 Key 注册）
    api_base_url: str = ""
    api_allow_insecure_internal: bool = False

    # 凭证存储配置
    credential_store: str = "persistent"  # 文件优先，环境变量用于容器注入

    # 存储配置
    data_dir: str = field(default_factory=lambda: str(DATA_ROOT))

    # 流控配置
    flow_control_enabled: bool = False  # 是否启用流控
    flow_control_strategy: str = "token_bucket"  # token_bucket/aimd/sliding_window
    flow_control_rate: float = 100.0  # 令牌补充速率（请求/秒）
    flow_control_capacity: int = 200  # 令牌桶容量

    # 安装 Key（用于快速注册，从环境变量 ANTCODE_WORKER_KEY 读取）
    worker_key: str = ""

    # 运行时信息
    start_time: datetime = field(default_factory=datetime.now)

    @property
    def venvs_dir(self) -> str:
        """虚拟环境存储目录"""
        return os.path.join(self.data_dir, "runtimes")

    @property
    def runs_dir(self) -> str:
        """任务执行目录"""
        return os.path.join(self.data_dir, "runs")

    def ensure_directories(self):
        """确保所有存储目录存在"""
        for dir_path in [
            self.venvs_dir,
            self.runs_dir,
        ]:
            os.makedirs(dir_path, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "port": self.port,
            "host": self.host,
            "region": self.region,
            "version": self.version,
            "description": self.description,
            "tags": self.tags,
            "heartbeat_interval": self.heartbeat_interval,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "task_timeout": self.task_timeout,
            "task_cpu_time_limit_sec": self.task_cpu_time_limit_sec,
            "task_memory_limit_mb": self.task_memory_limit_mb,
            "auto_resource_limit": self.auto_resource_limit,
            "transport_mode": self.transport_mode,
            "redis_url": self.redis_url,
            "redis_namespace": self.redis_namespace,
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "gateway_tls": self.gateway_tls,
            "ca_cert": self.ca_cert,
            "client_cert": self.client_cert,
            "client_key": self.client_key,
            "sandbox_mode": self.sandbox_mode,
            "sandbox_command": self.sandbox_command,
            "sandbox_network_isolated": self.sandbox_network_isolated,
            "api_base_url": self.api_base_url,
            "api_allow_insecure_internal": self.api_allow_insecure_internal,
            "credential_store": self.credential_store,
            "data_dir": self.data_dir,
            "flow_control_enabled": self.flow_control_enabled,
            "flow_control_strategy": self.flow_control_strategy,
            "flow_control_rate": self.flow_control_rate,
            "flow_control_capacity": self.flow_control_capacity,
            "start_time": self.start_time.isoformat(),
        }

    def _get_config_data(self) -> dict[str, Any]:
        """获取配置数据字典（内部方法）"""
        return {
            "name": self.name,
            "port": self.port,
            "region": self.region,
            "description": self.description,
            "tags": self.tags,
            "heartbeat_interval": self.heartbeat_interval,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "task_timeout": self.task_timeout,
            "task_cpu_time_limit_sec": self.task_cpu_time_limit_sec,
            "task_memory_limit_mb": self.task_memory_limit_mb,
            "transport_mode": self.transport_mode,
            "redis_url": self.redis_url,
            "redis_namespace": self.redis_namespace,
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "gateway_tls": self.gateway_tls,
            "ca_cert": self.ca_cert,
            "client_cert": self.client_cert,
            "client_key": self.client_key,
            "api_base_url": self.api_base_url,
            "api_allow_insecure_internal": self.api_allow_insecure_internal,
            "credential_store": self.credential_store,
            "data_dir": self.data_dir,
            "flow_control_enabled": self.flow_control_enabled,
            "flow_control_strategy": self.flow_control_strategy,
            "flow_control_rate": self.flow_control_rate,
            "flow_control_capacity": self.flow_control_capacity,
        }

    def save_to_file(self, path: Path | None = None) -> None:
        """保存配置到文件（同步版本，用于启动时）"""
        path = path or WORKER_CONFIG_FILE
        config_data = self._get_config_data()
        yaml_content = yaml.dump(config_data, allow_unicode=True, default_flow_style=False)
        _atomic_write_private_config(path, yaml_content)

    async def save_to_file_async(self, path: Path | None = None) -> None:
        """保存配置到文件（异步版本，用于运行时更新）"""
        path = path or WORKER_CONFIG_FILE
        config_data = self._get_config_data()
        yaml_content = yaml.dump(config_data, allow_unicode=True, default_flow_style=False)
        await asyncio.to_thread(_atomic_write_private_config, path, yaml_content)

    @classmethod
    def load_from_file(cls, path: Path | None = None) -> "WorkerConfig":
        """从文件加载配置（同步版本，用于启动时）"""
        path = path or WORKER_CONFIG_FILE
        if not path.exists():
            return cls()

        try:
            with open(path, encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
            return cls(**{k: v for k, v in config_data.items() if v is not None})
        except Exception as e:
            raise RuntimeError(f"加载配置文件失败: {path}") from e

    @classmethod
    async def load_from_file_async(cls, path: Path | None = None) -> "WorkerConfig":
        """从文件加载配置（异步版本，用于运行时重载）"""
        path = path or WORKER_CONFIG_FILE
        if not path.exists():
            return cls()

        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                content = await f.read()
            config_data = yaml.safe_load(content) or {}
            return cls(**{k: v for k, v in config_data.items() if v is not None})
        except Exception as e:
            raise RuntimeError(f"异步加载配置文件失败: {path}") from e


# 全局配置实例
_worker_config: WorkerConfig | None = None


def get_worker_config() -> WorkerConfig:
    """获取全局Worker配置"""
    global _worker_config
    if _worker_config is None:
        _worker_config = WorkerConfig.load_from_file()
    return _worker_config


def set_worker_config(config: WorkerConfig):
    """设置全局Worker配置"""
    global _worker_config
    _worker_config = config


def calculate_adaptive_limits() -> dict[str, int]:
    """
    根据系统资源自适应计算任务限制

    算法：
    - max_concurrent_tasks: min(CPU核心数, 可用内存GB / 2, 10)
    - task_memory_limit_mb: 可用内存 / (并发数 * 1.5)，预留 30% 给系统
    - task_cpu_time_limit_sec: 基于任务超时时间的 80%
    """
    import psutil

    try:
        cpu_count = psutil.cpu_count() or os.cpu_count() or 4
        mem = psutil.virtual_memory()
        total_mem_gb = mem.total / (1024**3)
    except Exception as exc:
        logger.warning("获取系统资源失败，使用默认资源限制: {}", exc)
        return DEFAULT_RESOURCE_LIMITS.copy()

    # 计算最大并发数：取 CPU 核心数、总内存/2GB、硬上限 10 的最小值
    max_concurrent = min(cpu_count, max(1, int(total_mem_gb / 2)), 10)

    # 计算单任务内存限制：总内存的 70% / 并发数，最小 512MB，最大 4GB
    usable_mem_mb = int(total_mem_gb * 0.7 * 1024)
    task_memory = max(512, min(4096, usable_mem_mb // max(1, max_concurrent)))

    # CPU 时间限制：默认 10 分钟，高性能机器可以更长
    task_cpu_time = min(1800, max(300, cpu_count * 60))

    return {
        "max_concurrent_tasks": max_concurrent,
        "task_memory_limit_mb": task_memory,
        "task_cpu_time_limit_sec": task_cpu_time,
    }


# 安全默认值（当 auto_resource_limit=false 且值为 0 时使用）
DEFAULT_RESOURCE_LIMITS = {
    "max_concurrent_tasks": 4,
    "task_memory_limit_mb": 512,
    "task_cpu_time_limit_sec": 300,
}


def apply_resource_limits(config: WorkerConfig) -> WorkerConfig:
    """
    应用资源限制，手动值优先

    优先级：手动设置值(>0) > 自动计算(auto=true) > 安全默认值

    Args:
        config: Worker配置对象

    Returns:
        应用资源限制后的配置对象
    """
    # 获取自动计算的推荐值（仅当 auto_resource_limit=true 时）
    adaptive = calculate_adaptive_limits() if config.auto_resource_limit else None

    # 并发数：手动(>0) > 自动 > 默认
    if config.max_concurrent_tasks <= 0:
        if adaptive:
            config.max_concurrent_tasks = adaptive["max_concurrent_tasks"]
        else:
            config.max_concurrent_tasks = DEFAULT_RESOURCE_LIMITS["max_concurrent_tasks"]

    # 内存限制：手动(>0) > 自动 > 默认
    if config.task_memory_limit_mb <= 0:
        if adaptive:
            config.task_memory_limit_mb = adaptive["task_memory_limit_mb"]
        else:
            config.task_memory_limit_mb = DEFAULT_RESOURCE_LIMITS["task_memory_limit_mb"]

    # CPU时间：手动(>0) > 自动 > 默认
    if config.task_cpu_time_limit_sec <= 0:
        if adaptive:
            config.task_cpu_time_limit_sec = adaptive["task_cpu_time_limit_sec"]
        else:
            config.task_cpu_time_limit_sec = DEFAULT_RESOURCE_LIMITS["task_cpu_time_limit_sec"]

    return config


def init_worker_config(name: str = "Worker-001", port: int = 8001, region: str = "默认", **kwargs) -> WorkerConfig:
    """
    初始化Worker配置

    启动时加载配置文件 worker_config.yaml（如果存在），
    命令行参数会覆盖配置文件中的值。

    资源限制优先级：手动设置值(>0) > 自动计算(auto=true) > 安全默认值

    Args:
        name: Worker名称
        port: 监听端口
        region: 区域标签
        **kwargs: 其他配置参数

    Returns:
        初始化后的Worker配置

    Requirements: 7.1
    """
    _load_env_file()

    # 读取环境变量配置
    env_config = _load_env_config()

    # 首先尝试从配置文件加载
    file_config: dict[str, Any] = {}
    if WORKER_CONFIG_FILE.exists():
        try:
            with open(WORKER_CONFIG_FILE, encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            logger.info("已加载配置文件: {}", WORKER_CONFIG_FILE)
        except Exception as e:
            logger.warning("加载配置文件失败: {}", e)

    # 合并配置：配置文件 < 环境变量
    merged_config = {**file_config, **env_config}

    # 规范化路径配置（所有路径统一归到 data/ 下；相对路径基于项目根目录）
    if merged_config.get("data_dir"):
        merged_config["data_dir"] = _normalize_path(str(merged_config["data_dir"]))

    for key in ("projects_dir", "venvs_dir", "runs_dir"):
        if merged_config.get(key):
            merged_config[key] = _normalize_path(str(merged_config[key]))

    # 命令行参数覆盖（仅当显式设置或上层未提供）
    if name != "Worker-001" or "name" not in merged_config:
        merged_config["name"] = name
    if port != 8001 or "port" not in merged_config:
        merged_config["port"] = port
    if region != "默认" or "region" not in merged_config:
        merged_config["region"] = region

    # 其他 kwargs 参数覆盖（默认值不覆盖 env/file）
    def _apply_override(key: str, value: Any, default: Any) -> None:
        if value != default or key not in merged_config:
            merged_config[key] = value

    if "host" in kwargs:
        _apply_override("host", kwargs["host"], "0.0.0.0")
    if "transport_mode" in kwargs:
        _apply_override("transport_mode", kwargs["transport_mode"], "gateway")
    if "redis_url" in kwargs:
        _apply_override("redis_url", kwargs["redis_url"], "")
    if "redis_namespace" in kwargs:
        _apply_override("redis_namespace", kwargs["redis_namespace"], "antcode")
    if "gateway_host" in kwargs:
        _apply_override("gateway_host", kwargs["gateway_host"], "localhost")
    if "gateway_port" in kwargs:
        _apply_override("gateway_port", kwargs["gateway_port"], 50051)

    # 其他 kwargs 参数覆盖
    for key, value in kwargs.items():
        if key in (
            "host",
            "transport_mode",
            "redis_url",
            "redis_namespace",
            "gateway_host",
            "gateway_port",
        ):
            continue
        merged_config[key] = value

    # 创建配置对象
    config = WorkerConfig(**{k: v for k, v in merged_config.items() if v is not None})

    # 应用资源限制（手动值优先）
    config = apply_resource_limits(config)

    auto_mode = "自动" if config.auto_resource_limit else "默认"
    logger.info(
        "资源限制: mode={} concurrent={} memory={}MB cpu_time={}s",
        auto_mode,
        config.max_concurrent_tasks,
        config.task_memory_limit_mb,
        config.task_cpu_time_limit_sec,
    )

    config.ensure_directories()
    set_worker_config(config)
    return config
