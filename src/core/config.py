"""应用配置模块"""
import os
from functools import cached_property
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_frontend_port() -> int:
    """获取前端端口，优先级：FRONTEND_PORT > VITE_PORT > 3000"""
    for env_key in ("FRONTEND_PORT", "VITE_PORT"):
        port_str = os.getenv(env_key, "").strip()
        if port_str.isdigit():
            return int(port_str)
    return 3000


class Settings(BaseSettings):
    """应用配置类，使用 cached_property 优化重复计算"""

    # === 服务器配置 ===
    DATABASE_URL: str = Field(default="")
    REDIS_URL: str = Field(default="")
    SERVER_HOST: str = Field(default="0.0.0.0")
    SERVER_PORT: int = Field(default=8000)
    FRONTEND_PORT: int = Field(default_factory=_default_frontend_port)
    SERVER_DOMAIN: str = Field(default="localhost")

    # === 日志配置 ===
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: str = Field(default="text")
    LOG_TO_FILE: bool = Field(default=True)

    # === 应用信息 ===
    APP_NAME: str = "AntCode API"
    APP_DESCRIPTION: str = "AntCode Task Scheduling Platform"
    APP_VERSION: str = "1.3.0"

    # === JWT 配置 ===
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    @property
    def JWT_SECRET_KEY(self) -> str:
        from src.core.security.auth import jwt_secret_manager
        return jwt_secret_manager.get_secret()

    # === CORS 配置 ===
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list = ["*"]
    CORS_ALLOW_HEADERS: list = ["*"]

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
    BASE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

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
    ALLOWED_FILE_TYPES: list = ['.zip', '.tar.gz', '.py', '.txt', '.json', '.md', '.yml', '.yaml']

    # === Master URL 配置 ===
    MASTER_URL: str = Field(default="")

    @cached_property
    def REDIS_ENABLED(self) -> bool:
        """Redis 是否启用（根据 REDIS_URL 自动判断）"""
        return bool(self.REDIS_URL and self.REDIS_URL.strip())

    @cached_property
    def master_url(self) -> str:
        """主节点 URL"""
        return self.MASTER_URL.rstrip('/') if self.MASTER_URL else f"http://{self.SERVER_HOST}:{self.SERVER_PORT}"

    # === 默认管理员 ===
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "Admin123!"

    # === 任务队列后端配置 ===
    # 队列后端类型: "memory" (默认) 或 "redis"
    QUEUE_BACKEND: str = Field(default="memory")

    # === Worker 日志缓冲配置 ===
    # 日志缓冲区大小（达到此数量触发批量发送）
    LOG_BUFFER_SIZE: int = Field(default=50)
    # 日志刷新间隔（秒）
    LOG_FLUSH_INTERVAL: float = Field(default=2.0)
    # 日志缓冲区最大行数（超过则丢弃最旧日志）
    LOG_BUFFER_MAX_LINES: int = Field(default=500)

    # === Worker 项目缓存配置 ===
    # 项目缓存最大数量
    PROJECT_CACHE_MAX_SIZE: int = Field(default=100)
    # 项目缓存 TTL（小时）
    PROJECT_CACHE_TTL_HOURS: int = Field(default=168)

    # === 调度器配置 ===
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
        """缓存是否使用 Redis（与 REDIS_ENABLED 相同）"""
        return self.REDIS_ENABLED

    @property
    def METRICS_USE_REDIS_CACHE(self) -> bool:
        return self.REDIS_ENABLED

    @property
    def USERS_USE_REDIS_CACHE(self) -> bool:
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
    MONITOR_STATUS_KEY_TPL: str = "monitor:node:{node_id}:status"
    MONITOR_SPIDER_KEY_TPL: str = "monitor:node:{node_id}:spider"
    MONITOR_HISTORY_KEY_TPL: str = "monitor:node:{node_id}:history"
    MONITOR_CLUSTER_SET_KEY: str = "monitor:cluster:nodes"

    # === 数据库连接池 ===
    DATABASE_MIN_CONNECTIONS: int = 20
    DATABASE_MAX_CONNECTIONS: int = 200

    # === 限流配置 ===
    RATE_LIMIT_CALLS: int = 1000
    RATE_LIMIT_PERIOD: int = 60

    # === WebSocket 配置 ===
    WEBSOCKET_MAX_CONN_PER_EXECUTION: int = 200
    WEBSOCKET_MAX_TOTAL_CONN: int = 20000

    # === gRPC 配置 ===
    # 是否启用 gRPC 服务
    GRPC_ENABLED: bool = Field(default=True)
    # gRPC 服务端口
    GRPC_PORT: int = Field(default=50051)
    # gRPC 最大工作线程数
    GRPC_MAX_WORKERS: int = Field(default=10)
    # gRPC 心跳间隔（秒）
    GRPC_HEARTBEAT_INTERVAL: int = Field(default=30)
    # gRPC 心跳超时（秒）- 超过此时间未收到心跳则标记节点离线
    GRPC_HEARTBEAT_TIMEOUT: int = Field(default=90)
    # gRPC 优雅关闭等待时间（秒）
    GRPC_SHUTDOWN_GRACE_PERIOD: float = Field(default=5.0)
    # gRPC TLS 证书路径（可选）
    GRPC_TLS_CERT_PATH: str = Field(default="")
    # gRPC TLS 密钥路径（可选）
    GRPC_TLS_KEY_PATH: str = Field(default="")

    # === gRPC 性能优化配置 ===
    # 日志缓冲区最大大小（行数）- 超过则触发背压丢弃最旧日志
    GRPC_LOG_BUFFER_MAX_SIZE: int = Field(default=2000)
    # 日志批次大小 - 达到此数量触发批量发送
    GRPC_LOG_BATCH_SIZE: int = Field(default=50)
    # 日志刷新间隔（秒）
    GRPC_LOG_FLUSH_INTERVAL: float = Field(default=1.0)
    # 压缩阈值（字节）- 超过此大小的消息将被压缩
    GRPC_COMPRESS_THRESHOLD: int = Field(default=1024)
    # 消息发送队列大小
    GRPC_SEND_QUEUE_SIZE: int = Field(default=1000)
    # 指标延迟样本最大数量
    GRPC_METRICS_MAX_LATENCY_SAMPLES: int = Field(default=100)

    # === 数据库解析（缓存 urlparse 结果）===
    @cached_property
    def _parsed_db_url(self):
        """缓存解析后的数据库 URL"""
        return urlparse(self.db_url)

    def _get_db_host(self) -> str:
        return self._parsed_db_url.hostname or 'localhost'

    def _get_db_port(self) -> int:
        port = self._parsed_db_url.port
        if port:
            return port
        return 3306 if 'mysql' in self.db_url.lower() else 5432

    def _get_db_user(self) -> str:
        return self._parsed_db_url.username or 'root'

    def _get_db_password(self) -> str:
        return self._parsed_db_url.password or ''

    def _get_db_name(self) -> str:
        db_name = self._parsed_db_url.path.lstrip('/')
        return db_name.split('?')[0] if '?' in db_name else (db_name or 'antcode')

    @cached_property
    def TORTOISE_ORM(self) -> dict:
        """Tortoise ORM 配置"""
        db_url_lower = self.db_url.lower()

        if db_url_lower.startswith('sqlite'):
            file_path = self.db_url.replace('sqlite:///', '')
            if not file_path.startswith('/'):
                file_path = os.path.join(self.data_dir, "db", os.path.basename(file_path))
            connection_config = {
                "engine": "tortoise.backends.sqlite",
                "credentials": {"file_path": file_path},
            }
        elif 'mysql' in db_url_lower or 'mariadb' in db_url_lower:
            connection_config = {
                "engine": "tortoise.backends.mysql",
                "credentials": {
                    "host": self._get_db_host(),
                    "port": self._get_db_port(),
                    "user": self._get_db_user(),
                    "password": self._get_db_password(),
                    "database": self._get_db_name(),
                    "charset": "utf8mb4",
                    "connect_timeout": 30,
                },
                "minsize": self.DATABASE_MIN_CONNECTIONS,
                "maxsize": self.DATABASE_MAX_CONNECTIONS,
                "pool_recycle": 1800,
            }
        elif 'postgres' in db_url_lower:
            connection_config = {
                "engine": "tortoise.backends.asyncpg",
                "credentials": {
                    "host": self._get_db_host(),
                    "port": self._get_db_port(),
                    "user": self._get_db_user(),
                    "password": self._get_db_password(),
                    "database": self._get_db_name(),
                },
                "minsize": self.DATABASE_MIN_CONNECTIONS,
                "maxsize": self.DATABASE_MAX_CONNECTIONS,
            }
        else:
            raise ValueError(f"不支持的数据库类型: {self.db_url}")

        return {
            "connections": {"default": connection_config},
            "apps": {
                "models": {
                    "models": ["src.models"],
                    "default_connection": "default",
                },
            },
            "use_tz": False,
            "timezone": "Asia/Shanghai",
        }

    model_config = SettingsConfigDict(
        env_file=os.path.join(
            os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)),
            ".env"
        ),
        case_sensitive=True,
        extra="ignore"
    )

    @model_validator(mode='after')
    def validate_queue_backend(self) -> 'Settings':
        """验证队列后端配置：当 QUEUE_BACKEND=redis 时，REDIS_URL 必须设置"""
        if self.QUEUE_BACKEND == "redis":
            if not self.REDIS_URL or not self.REDIS_URL.strip():
                raise ValueError(
                    "QUEUE_BACKEND 设置为 'redis' 时，REDIS_URL 必须设置。"
                    "请在 .env 文件中配置 REDIS_URL，格式: redis://[:password]@host:port/db"
                )
        return self


settings = Settings()
