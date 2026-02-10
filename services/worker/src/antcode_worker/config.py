"""
工作Worker配置模块

提供Worker配置管理，支持同步和异步文件操作。

Requirements: 7.1
"""

import contextlib
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles
import yaml
from loguru import logger

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

# Worker根目录
WORKER_ROOT = Path(__file__).parent
SERVICE_ROOT = WORKER_ROOT.parent.parent  # services/worker/src -> services/worker
# 项目根目录（运行时数据统一放在 data/ 下）
PROJECT_ROOT = SERVICE_ROOT.parent.parent  # services/worker -> project root
DATA_ROOT = PROJECT_ROOT / "data" / "worker"

# Worker配置文件路径
WORKER_CONFIG_FILE = DATA_ROOT / "worker_config.yaml"

_ENV_LOADED = False


def _load_env_file() -> None:
    """加载 .env 环境变量（仅一次）"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    if not load_dotenv:
        return

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


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
    return str(path)


def _load_env_config() -> dict[str, Any]:
    """读取环境变量配置"""
    env_config: dict[str, Any] = {}

    api_base_url = _get_env_value(
        "WORKER_API_BASE_URL",
        "ANTCODE_API_BASE_URL",
        "API_BASE_URL",
    )
    if api_base_url:
        env_config["api_base_url"] = api_base_url

    transport_mode = _get_env_value("WORKER_TRANSPORT_MODE", "TRANSPORT_MODE", "ANTCODE_TRANSPORT_MODE")
    if transport_mode:
        env_config["transport_mode"] = transport_mode

    redis_url = _get_env_value("WORKER_REDIS_URL", "REDIS_URL", "ANTCODE_REDIS_URL")
    if redis_url:
        env_config["redis_url"] = redis_url

    redis_namespace = _get_env_value("WORKER_REDIS_NAMESPACE", "REDIS_NAMESPACE")
    if redis_namespace:
        env_config["redis_namespace"] = redis_namespace

    gateway_endpoint = _get_env_value("WORKER_GATEWAY_ENDPOINT", "GATEWAY_ENDPOINT", "ANTCODE_GATEWAY_ENDPOINT")
    if gateway_endpoint:
        if ":" in gateway_endpoint:
            host, port = gateway_endpoint.rsplit(":", 1)
            env_config["gateway_host"] = host
            with contextlib.suppress(ValueError):
                env_config["gateway_port"] = int(port)
        else:
            env_config["gateway_host"] = gateway_endpoint

    gateway_host = _get_env_value("WORKER_GATEWAY_HOST", "GATEWAY_HOST", "ANTCODE_GATEWAY_HOST")
    if gateway_host:
        env_config["gateway_host"] = gateway_host

    gateway_port = _get_env_int("WORKER_GATEWAY_PORT", "GATEWAY_PORT", "ANTCODE_GATEWAY_PORT")
    if gateway_port is not None:
        env_config["gateway_port"] = gateway_port

    name = _get_env_value("WORKER_NAME", "ANTCODE_WORKER_NAME")
    if name:
        env_config["name"] = name

    host = _get_env_value("WORKER_HOST", "ANTCODE_WORKER_HOST")
    if host:
        env_config["host"] = host

    port = _get_env_int("WORKER_PORT", "ANTCODE_WORKER_PORT")
    if port is not None:
        env_config["port"] = port

    region = _get_env_value("WORKER_REGION", "ANTCODE_WORKER_REGION")
    if region:
        env_config["region"] = region

    heartbeat_interval = _get_env_int("WORKER_HEARTBEAT_INTERVAL", "ANTCODE_HEARTBEAT_INTERVAL")
    if heartbeat_interval is not None:
        env_config["heartbeat_interval"] = heartbeat_interval

    max_concurrent = _get_env_int("WORKER_MAX_CONCURRENT_TASKS", "MAX_CONCURRENT_TASKS", "ANTCODE_MAX_CONCURRENT_TASKS")
    if max_concurrent is not None:
        env_config["max_concurrent_tasks"] = max_concurrent

    data_dir = _get_env_value("WORKER_DATA_DIR", "ANTCODE_WORKER_DATA_DIR")
    if data_dir:
        env_config["data_dir"] = data_dir

    credential_store = _get_env_value("WORKER_CREDENTIAL_STORE", "ANTCODE_WORKER_CREDENTIAL_STORE")
    if credential_store:
        env_config["credential_store"] = credential_store

    log_retention_days = _get_env_int("WORKER_LOG_RETENTION_DAYS")
    if log_retention_days is not None:
        env_config["log_retention_days"] = log_retention_days

    log_cleanup_interval_hours = _get_env_int("WORKER_LOG_CLEANUP_INTERVAL_HOURS")
    if log_cleanup_interval_hours is not None:
        env_config["log_cleanup_interval_hours"] = log_cleanup_interval_hours

    log_cleanup_enabled = _get_env_bool("WORKER_LOG_CLEANUP_ENABLED")
    if log_cleanup_enabled is not None:
        env_config["log_cleanup_enabled"] = log_cleanup_enabled

    # Worker 安装 Key（用于快速注册）
    worker_key = _get_env_value("ANTCODE_WORKER_KEY", "WORKER_KEY")
    if worker_key:
        env_config["worker_key"] = worker_key

    return env_config


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
    task_memory_limit_mb: int = 0  # 单任务内存上限（MB，0=自动）
    auto_resource_limit: bool = True  # 是否启用自适应资源限制

    # 传输模式配置
    transport_mode: str = "gateway"  # 传输模式: "direct" 或 "gateway"

    # Redis 配置（Direct 模式）
    redis_url: str = "redis://localhost:6379/0"
    redis_namespace: str = "antcode"

    # Gateway 配置（Gateway 模式）
    gateway_host: str = "localhost"
    gateway_port: int = 50051

    # 控制平面 API 地址（用于安装 Key 注册）
    api_base_url: str = ""

    # 凭证存储配置
    credential_store: str = "file"  # 凭证存储类型: "file" (默认) 或 "env"

    # 存储配置
    data_dir: str = field(default_factory=lambda: str(DATA_ROOT))

    # 日志清理配置
    log_retention_days: int = 7  # Worker 端日志保留天数（默认 7 天）
    log_cleanup_interval_hours: int = 24  # 日志清理间隔（小时）
    log_cleanup_enabled: bool = True  # 是否启用日志清理

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
    def projects_dir(self) -> str:
        """项目存储目录"""
        return os.path.join(self.data_dir, "projects")

    @property
    def venvs_dir(self) -> str:
        """虚拟环境存储目录"""
        return os.path.join(self.data_dir, "runtimes")

    @property
    def logs_dir(self) -> str:
        """日志存储目录"""
        return os.path.join(self.data_dir, "logs")

    @property
    def runs_dir(self) -> str:
        """任务执行目录"""
        return os.path.join(self.data_dir, "runs")

    def ensure_directories(self):
        """确保所有存储目录存在"""
        for dir_path in [
            self.projects_dir,
            self.venvs_dir,
            self.logs_dir,
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
            "api_base_url": self.api_base_url,
            "credential_store": self.credential_store,
            "data_dir": self.data_dir,
            "log_retention_days": self.log_retention_days,
            "log_cleanup_interval_hours": self.log_cleanup_interval_hours,
            "log_cleanup_enabled": self.log_cleanup_enabled,
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
            "api_base_url": self.api_base_url,
            "credential_store": self.credential_store,
            "data_dir": self.data_dir,
            "log_retention_days": self.log_retention_days,
            "log_cleanup_interval_hours": self.log_cleanup_interval_hours,
            "log_cleanup_enabled": self.log_cleanup_enabled,
            "flow_control_enabled": self.flow_control_enabled,
            "flow_control_strategy": self.flow_control_strategy,
            "flow_control_rate": self.flow_control_rate,
            "flow_control_capacity": self.flow_control_capacity,
        }

    def save_to_file(self, path: Path | None = None) -> None:
        """保存配置到文件（同步版本，用于启动时）"""
        path = path or WORKER_CONFIG_FILE
        config_data = self._get_config_data()
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

    async def save_to_file_async(self, path: Path | None = None) -> None:
        """保存配置到文件（异步版本，用于运行时更新）"""
        path = path or WORKER_CONFIG_FILE
        config_data = self._get_config_data()
        yaml_content = yaml.dump(config_data, allow_unicode=True, default_flow_style=False)
        os.makedirs(path.parent, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(yaml_content)

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
            logger.warning("加载配置文件失败: {}", e)
            return cls()

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
            logger.warning("异步加载配置文件失败: {}", e)
            return cls()


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


def init_worker_config(
    name: str = "Worker-001",
    port: int = 8001,
    region: str = "默认",
    **kwargs
) -> WorkerConfig:
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
    file_config = {}
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

    for key in ("projects_dir", "venvs_dir", "logs_dir", "runs_dir", "wal_dir", "spool_dir"):
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
        _apply_override("redis_url", kwargs["redis_url"], "redis://localhost:6379/0")
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
