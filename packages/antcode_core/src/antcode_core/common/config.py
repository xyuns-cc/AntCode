"""应用配置模块

提供统一的配置管理，支持环境变量和 .env 文件。
"""

import os
from functools import cached_property
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_frontend_port() -> int:
    """获取前端端口，优先级：FRONTEND_PORT > VITE_PORT > 3000"""
    for env_key in ("FRONTEND_PORT", "VITE_PORT"):
        port_str = os.getenv(env_key, "").strip()
        if port_str.isdigit():
            return int(port_str)
    return 3000


def _find_project_root() -> Path:
    """查找项目根目录（包含 .env 文件的目录，或最顶层的 pyproject.toml）"""
    current = Path(__file__).resolve()

    # 首先尝试找包含 .env 的目录
    for parent in current.parents:
        if (parent / ".env").exists():
            return parent

    # 回退：找最顶层的 pyproject.toml（跳过子包的 pyproject.toml）
    root = None
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            root = parent

    if root:
        return root

    # 最后回退到当前文件的上级目录
    return current.parent.parent.parent.parent.parent.parent


class Settings(BaseSettings):
    """应用配置类，使用 cached_property 优化重复计算"""

    # === 服务器配置 ===
    DATABASE_URL: str = Field(default="")
    REDIS_URL: str = Field(default="")
    REDIS_NAMESPACE: str = Field(default="antcode")
    API_BASE_URL: str = Field(default="")
    SERVER_HOST: str = Field(default="0.0.0.0")
    SERVER_PORT: int = Field(default=8000)
    SERVER_RELOAD: bool = Field(default=False)
    FRONTEND_PORT: int = Field(default_factory=_default_frontend_port)
    SERVER_DOMAIN: str = Field(default="localhost")

    # === 日志配置 ===
    LOG_LEVEL: str = Field(default="INFO")
    LOG_TO_FILE: bool = Field(default=True)

    # === 应用信息 ===
    APP_NAME: str = "AntCode"
    APP_TITLE: str = "AntCode 任务调度平台"
    APP_DESCRIPTION: str = "基于 FastAPI 的分布式任务调度和项目管理平台"
    APP_VERSION: str = "1.3.0"
    COPYRIGHT_YEAR: str = "2025"

    # === JWT 配置 ===
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_SECRET_FILE: str = Field(default="")

    # === 登录密码加密配置 ===
    LOGIN_PASSWORD_ENCRYPTION_ENABLED: bool = Field(default=True)
    LOGIN_PASSWORD_ENCRYPTION_REQUIRED: bool = Field(default=True)
    LOGIN_RSA_PRIVATE_KEY_FILE: str = Field(default="")
    LOGIN_RSA_PUBLIC_KEY_FILE: str = Field(default="")
    LOGIN_RSA_KEY_ID: str = Field(default="")
    LOGIN_RSA_KEY_SIZE: int = Field(default=2048)

    # === CORS 配置 ===
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = ["*"]
    CORS_ALLOW_HEADERS: list[str] = ["*"]

    @cached_property
    def CORS_ORIGINS(self) -> list[str]:
        origins = [
            f"http://{self.SERVER_DOMAIN}:{self.FRONTEND_PORT}",
            f"http://localhost:{self.FRONTEND_PORT}",
            f"http://127.0.0.1:{self.FRONTEND_PORT}",
        ]
        if self.SERVER_DOMAIN not in ("localhost", "127.0.0.1"):
            origins.append(f"https://{self.SERVER_DOMAIN}")
        return origins

    # === 路径配置 ===
    BASE_DIR: str = Field(default_factory=lambda: str(_find_project_root()))

    @cached_property
    def data_dir(self) -> str:
        """数据目录"""
        return os.path.join(self.BASE_DIR, "data")

    @cached_property
    def db_url(self) -> str:
        """数据库连接 URL"""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"sqlite:///{os.path.join(self.data_dir, 'db', 'antcode.sqlite3')}"

    @cached_property
    def LOG_FILE_PATH(self) -> str:
        return os.path.join(self.data_dir, "logs", "app.log")

    @cached_property
    def LOCAL_STORAGE_PATH(self) -> str:
        return os.path.join(self.data_dir, "storage", "projects")

    # === 文件配置 ===
    MAX_FILE_SIZE: int = 100 * 1024 * 1024
    MAX_FILE_EDIT_SIZE: int = 1 * 1024 * 1024
    ALLOWED_FILE_TYPES: list[str] = [
        ".zip",
        ".tar.gz",
        ".py",
        ".txt",
        ".json",
        ".md",
        ".yml",
        ".yaml",
    ]

    # === Master URL 配置 ===
    GATEWAY_HOST: str = Field(default="localhost")
    GATEWAY_PORT: int = Field(default=50051)
    WORKER_TRANSPORT_MODE: str = Field(default="gateway")
    WORKER_HEARTBEAT_TIMEOUT: int = Field(default=60)
    WORKER_HEARTBEAT_INTERVAL_ONLINE: int = Field(default=3)
    WORKER_HEARTBEAT_INTERVAL_OFFLINE: int = Field(default=60)
    WORKER_HEARTBEAT_MAX_FAILURES: int = Field(default=5)
    WORKER_HEARTBEAT_TIMEOUT_REQUEST: int = Field(default=2)

    @cached_property
    def REDIS_ENABLED(self) -> bool:
        """Redis 是否启用（根据 REDIS_URL 自动判断）"""
        return bool(self.REDIS_URL and self.REDIS_URL.strip())

    # === 默认管理员 ===
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "Admin123!"

    # === 抽象后端配置 ===
    CRAWL_BACKEND: str = Field(default="memory")
    FILE_STORAGE_BACKEND: str = Field(default="local")

    # === 日志双通道传输配置 ===
    LOG_CHUNK_SIZE: int = Field(default=131072)
    LOG_CHUNK_INTERVAL: float = Field(default=1.0)
    LOG_MAX_IN_FLIGHT: int = Field(default=8)
    LOG_ACK_TIMEOUT: float = Field(default=5.0)
    LOG_RETRY_BASE: float = Field(default=0.5)
    LOG_RETRY_MAX_DELAY: float = Field(default=5.0)
    LOG_RETRY_MAX: int = Field(default=5)
    LOG_WORKER_MAX_RATE: int = Field(default=800 * 1024)
    WORKER_LOG_RETENTION_DAYS: int = Field(default=7)
    LOG_STREAM_MAXLEN: int = Field(default=10000)
    LOG_STREAM_TTL_SECONDS: int = Field(default=7 * 86400)
    LOG_CHUNK_STREAM_MAXLEN: int = Field(default=2000)
    LOG_CHUNK_TTL_SECONDS: int = Field(default=7 * 86400)
    LOG_ARCHIVE_PREFIX: str = Field(default="logs")
    LOG_ARCHIVE_RETENTION_DAYS: int = Field(default=30)

    # === 日志持久化存储配置 ===
    LOG_STORAGE_BACKEND: str = Field(default="s3")  # s3, local, clickhouse

    # === 调度器配置 ===
    SCHEDULER_ROLE: str = Field(default="master")
    SCHEDULER_EVENT_STREAM: str = Field(default="scheduler:events")
    SCHEDULER_EVENT_GROUP: str = Field(default="scheduler-events")
    SCHEDULER_EVENT_MAXLEN: int = Field(default=10000)
    SCHEDULER_TIMEZONE: str = "Asia/Shanghai"
    MAX_CONCURRENT_TASKS: int = 10
    TASK_EXECUTION_TIMEOUT: int = 3600
    TASK_CPU_TIME_LIMIT_SEC: int = 600
    TASK_MEMORY_LIMIT_MB: int = 1024
    TASK_MAX_RETRIES: int = 3
    TASK_RETRY_DELAY: int = 60
    TASK_LOG_RETENTION_DAYS: int = 30
    TASK_LOG_MAX_SIZE: int = 100 * 1024 * 1024

    @cached_property
    def TASK_LOG_DIR(self) -> str:
        return os.path.join(self.data_dir, "logs", "tasks")

    @cached_property
    def TASK_EXECUTION_WORK_DIR(self) -> str:
        return os.path.join(self.data_dir, "storage", "executions")

    @cached_property
    def scheduler_event_stream(self) -> str:
        if self.REDIS_NAMESPACE:
            return f"{self.REDIS_NAMESPACE}:{self.SCHEDULER_EVENT_STREAM}"
        return self.SCHEDULER_EVENT_STREAM

    @cached_property
    def VENV_STORAGE_ROOT(self) -> str:
        return os.path.join(self.data_dir, "storage", "venvs")

    @cached_property
    def MISE_DATA_ROOT(self) -> str:
        return os.path.join(self.data_dir, "storage", "mise")

    # === 清理配置 ===
    CLEANUP_WORKSPACE_ON_COMPLETION: bool = True
    CLEANUP_WORKSPACE_MAX_AGE_HOURS: int = 24

    # === 缓存配置 ===
    CACHE_ENABLED: bool = True
    CACHE_DEFAULT_TTL: int = 300
    METRICS_CACHE_TTL: int = 30
    USERS_CACHE_TTL: int = 300
    API_CACHE_TTL: int = 300
    QUERY_CACHE_TTL: int = 300
    METRICS_BACKGROUND_UPDATE: bool = True
    METRICS_UPDATE_INTERVAL: int = 15

    @property
    def CACHE_USE_REDIS(self) -> bool:
        return self.REDIS_ENABLED

    @property
    def METRICS_USE_REDIS_CACHE(self) -> bool:
        return self.REDIS_ENABLED

    # === 监控配置 ===
    MONITORING_ENABLED: bool = True
    MONITOR_STATUS_TTL: int = 300
    MONITOR_HISTORY_TTL: int = 3600
    MONITOR_CLUSTER_TTL: int = 300
    MONITOR_STREAM_KEY: str = "monitor:stream:metrics"
    MONITOR_STREAM_LAST_ID_KEY: str = "monitor:stream:last_id"
    MONITOR_STREAM_BATCH_SIZE: int = 100
    MONITOR_STREAM_INTERVAL: int = 120
    MONITOR_HISTORY_KEEP_DAYS: int = 30
    MONITOR_STREAM_MAXLEN: int = 10000
    MONITOR_STATUS_KEY_TPL: str = "monitor:worker:{worker_id}:status"
    MONITOR_SPIDER_KEY_TPL: str = "monitor:worker:{worker_id}:spider"
    MONITOR_HISTORY_KEY_TPL: str = "monitor:worker:{worker_id}:history"
    MONITOR_CLUSTER_SET_KEY: str = "monitor:cluster:workers"

    # === 限流配置 ===
    RATE_LIMIT_CALLS: int = 1000
    RATE_LIMIT_PERIOD: int = 60

    # === WebSocket 配置 ===
    WEBSOCKET_MAX_CONN_PER_EXECUTION: int = 200
    WEBSOCKET_MAX_TOTAL_CONN: int = 20000

    model_config = SettingsConfigDict(
        env_file=str(_find_project_root() / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_backend_config(self) -> "Settings":
        """验证后端配置：当使用 redis 后端时，REDIS_URL 必须设置"""
        if self.CRAWL_BACKEND == "redis" and (not self.REDIS_URL or not self.REDIS_URL.strip()):
            raise ValueError(
                "CRAWL_BACKEND 设置为 'redis' 时，REDIS_URL 必须设置。"
                "请在 .env 文件中配置 REDIS_URL，格式: redis://[:password]@host:port/db"
            )
        if self.LOGIN_PASSWORD_ENCRYPTION_REQUIRED and not self.LOGIN_PASSWORD_ENCRYPTION_ENABLED:
            raise ValueError("LOGIN_PASSWORD_ENCRYPTION_REQUIRED 需同时启用 LOGIN_PASSWORD_ENCRYPTION_ENABLED")
        return self


# 全局配置实例
settings = Settings()
