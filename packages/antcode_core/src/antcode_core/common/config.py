"""应用配置模块

提供统一的配置管理，支持环境变量和 .env 文件。
"""

import os
from functools import cached_property
from pathlib import Path
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


def _validate_database_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL 只能使用 PostgreSQL 连接串。")
    if not parsed.hostname:
        raise ValueError("DATABASE_URL 缺少 host。")
    if not parsed.username:
        raise ValueError("DATABASE_URL 缺少 user。")
    if not parsed.password:
        raise ValueError("DATABASE_URL 缺少 password。")
    if not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL 缺少 database。")


def _validate_redis_url(value: str) -> None:
    parsed = urlparse(value)
    # T6-T1: 支持集群 (`redis+cluster://`) 与哨兵 (`redis+sentinel://`)。
    # scheme 决定 create_async_redis_client 走哪条路径。
    allowed_schemes = {
        "redis",
        "rediss",
        "redis+cluster",
        "rediss+cluster",
        "redis+sentinel",
        "rediss+sentinel",
    }
    if parsed.scheme not in allowed_schemes:
        raise ValueError(
            f"REDIS_URL scheme 不支持: {parsed.scheme!r}，仅允许 {sorted(allowed_schemes)}"
        )
    if not parsed.hostname:
        raise ValueError("REDIS_URL 缺少 host。")


class Settings(BaseSettings):
    """应用配置类，使用 cached_property 优化重复计算"""

    # === 服务器配置 ===
    DATABASE_URL: str = Field(default="")
    REDIS_URL: str = Field(default="")
    REDIS_NAMESPACE: str = Field(default="antcode")
    # 是否启用 Redis（关闭时依赖 Redis 的功能会 no-op：批次事件不发布、
    # scheduler_event_loop 空转、WS 日志不接实时流）。默认 true 与代码假设一致；
    # 空 REDIS_URL 场景可设 false 让功能显式降级而不是抛 AttributeError。
    REDIS_ENABLED: bool = Field(default=True)
    # T6-T1: Redis 部署形态。默认 auto 让 factory 从 URL scheme 判断
    # （redis+cluster / redis+sentinel）；也可显式覆盖为 standalone / cluster
    # / sentinel。用于 URL scheme 是标准 redis:// 但实际背后是集群/哨兵的
    # 反代场景。
    REDIS_MODE: str = Field(default="")
    REDIS_SENTINEL_MASTER_NAME: str = Field(default="mymaster")
    API_BASE_URL: str = Field(default="")
    BIND_HOST: str = Field(default="0.0.0.0")
    SERVER_PORT: int = Field(default=8000)
    SERVER_RELOAD: bool = Field(default=False)
    # uvicorn worker 进程数。默认 2：保守值，与单机跑多服务、K8s 2 vCPU
    # pod 常见形态对齐。IO-bound 场景需要更高吞吐时通过 env SERVER_WORKERS
    # 覆盖，压测参考 workers=4 时 RPS ~3x、MEM ~4x。生产扩容优先加实例数
    # 而非无脑加 workers（每 worker 独立 DB 池，撞 max_connections 更快）。
    # >1 时自动禁用 --reload。
    SERVER_WORKERS: int = Field(default=2)
    FRONTEND_PORT: int = Field(default_factory=_default_frontend_port)
    SERVER_DOMAIN: str = Field(default="localhost")

    # === 日志配置 ===
    LOG_LEVEL: str = Field(default="INFO")

    # === 应用信息 ===
    APP_NAME: str = "AntCode"
    APP_TITLE: str = "AntCode 任务调度平台"
    APP_DESCRIPTION: str = "基于 FastAPI 的分布式任务调度和项目管理平台"
    APP_VERSION: str = "1.0.0"
    COPYRIGHT_YEAR: str = "2025"

    # === JWT 配置 ===
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_SECRET_FILE: str = Field(default="")

    # === 加密密钥（独立于 JWT Secret）===
    ENCRYPTION_KEY: str = Field(default="")
    # T7-P2-5: 密钥轮换。逗号分隔的旧密钥（Fernet 派生前的原文），仅用于解
    # 密老密文。加密永远用当前 ENCRYPTION_KEY。轮换后一段时间可清空。
    ENCRYPTION_KEYS_LEGACY: str = Field(default="")

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
        """后端数据目录"""
        return os.path.join(self.BASE_DIR, "data", "backend")

    @cached_property
    def public_api_base_url(self) -> str:
        explicit_url = self.API_BASE_URL.strip().rstrip("/")
        if explicit_url:
            return explicit_url
        domain = self.SERVER_DOMAIN.strip() or "localhost"
        return f"http://{domain}:{self.SERVER_PORT}"

    @cached_property
    def db_url(self) -> str:
        """数据库连接 URL"""
        return self.DATABASE_URL

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
    WORKER_INSTALL_KEY_REPLAY_WINDOW_SECONDS: int = Field(default=60)
    WORKER_INSTALL_KEY_FAIL_THRESHOLD: int = Field(default=5)
    WORKER_INSTALL_KEY_BLOCK_SECONDS: int = Field(default=600)

    # === 默认管理员 ===
    DEFAULT_ADMIN_USERNAME: str = Field(default="admin")
    DEFAULT_ADMIN_PASSWORD: str = Field(default="")

    # === 爬虫数据通道 ===
    CRAWL_BACKEND: str = Field(default="redis")

    # === 日志双通道传输配置 ===
    LOG_CHUNK_SIZE: int = Field(default=131072)
    LOG_CHUNK_INTERVAL: float = Field(default=1.0)
    LOG_MAX_IN_FLIGHT: int = Field(default=8)
    LOG_ACK_TIMEOUT: float = Field(default=5.0)
    LOG_RETRY_BASE: float = Field(default=0.5)
    LOG_RETRY_MAX_DELAY: float = Field(default=5.0)
    LOG_RETRY_MAX: int = Field(default=5)
    LOG_WORKER_MAX_RATE: int = Field(default=800 * 1024)
    LOG_STREAM_MAXLEN: int = Field(default=10000)
    LOG_STREAM_TTL_SECONDS: int = Field(default=7 * 86400)
    LOG_CHUNK_STREAM_MAXLEN: int = Field(default=2000)
    LOG_CHUNK_TTL_SECONDS: int = Field(default=7 * 86400)

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
    ARTIFACT_RETENTION_DAYS: int = 30
    # T7-B2a (P0-2): audit_logs / worker_events 之前无保留策略——线性增长
    # 拖慢 audit 查询与 VACUUM。默认 90 / 30 天，运维可覆盖。
    AUDIT_LOG_RETENTION_DAYS: int = 90
    WORKER_EVENT_RETENTION_DAYS: int = 30
    # 单次批式 DELETE 上限，防止长事务锁表；一轮 cleanup 内多次循环直到删完
    LOG_CLEANUP_BATCH_LIMIT: int = 5000
    # T7-B3a (P1-1): 派发失败自动补派参数
    REDISPATCH_MAX_ATTEMPTS: int = 5
    REDISPATCH_BASE_DELAY_SEC: int = 30
    REDISPATCH_MAX_DELAY_SEC: int = 300
    REDISPATCH_TICK_INTERVAL_SEC: int = 10
    # T7-B4a (P1-2): 登录专项限流 + 账户锁定
    LOGIN_RATE_LIMIT_IP_MAX: int = 5           # 每 IP 每分钟最多 5 次尝试
    LOGIN_RATE_LIMIT_IP_PERIOD: int = 60
    LOGIN_RATE_LIMIT_USER_MAX: int = 10        # 每用户名每 15 分钟最多 10 次
    LOGIN_RATE_LIMIT_USER_PERIOD: int = 900
    LOGIN_LOCKOUT_FAILURES: int = 5            # 连续失败 N 次触发锁定
    LOGIN_LOCKOUT_DURATION_SEC: int = 900      # 锁定 15 分钟

    # 运维日志（loguru sink）：默认只写 stderr；置 True 时额外落文件（500MB rotate / 30 天）。
    # 与业务任务日志（task_logs 表）无关；容器化部署一般让日志由 stdout 采集，故默认 False。
    LOG_TO_FILE: bool = False
    LOG_FILE_PATH: str = "logs/app.log"

    # === Redis 熔断器 ===
    REDIS_CIRCUIT_BREAKER_ENABLED: bool = True
    REDIS_CIRCUIT_FAILURE_THRESHOLD: int = 5
    REDIS_CIRCUIT_RECOVERY_SECONDS: float = 30.0

    # === Direct Worker Redis ACL（per-worker 隔离） ===
    REDIS_ACL_ENABLED: bool = True
    REDIS_ACL_SAVE_INTERVAL_SECONDS: int = 300

    # === 数据库连接池配置 ===
    # 每服务独立 maxsize，避免单实例耗尽 PG max_connections。
    # 未识别的 service 名走全局 DB_POOL_MAX。
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 10
    DB_POOL_MAX_WEB_API: int = 20
    DB_POOL_MAX_MASTER: int = 15
    DB_POOL_MAX_GATEWAY: int = 10
    DB_POOL_MAX_WORKER: int = 5

    def db_pool_max_for(self, service: str | None) -> int:
        """根据服务名返回 maxsize；未知服务回退到全局 DB_POOL_MAX。"""
        if not service:
            return self.DB_POOL_MAX
        key = f"DB_POOL_MAX_{service.upper().replace('-', '_')}"
        return int(getattr(self, key, self.DB_POOL_MAX))

    @cached_property
    def TASK_EXECUTION_WORK_DIR(self) -> str:
        return os.path.join(self.data_dir, "work", "executions")

    @cached_property
    def scheduler_event_stream(self) -> str:
        if self.REDIS_NAMESPACE:
            return f"{self.REDIS_NAMESPACE}:{self.SCHEDULER_EVENT_STREAM}"
        return self.SCHEDULER_EVENT_STREAM

    @cached_property
    def MISE_DATA_ROOT(self) -> str:
        return os.path.join(self.data_dir, "work", "mise")

    @cached_property
    def LANG_CACHE_ROOT(self) -> str:
        """多语言依赖 cache 根目录，各语言子目录做隔离。

        - node: 用作 npm/pnpm cache 目录（`npm_config_cache` / `PNPM_STORE_PATH`）
        - go:   用作 `GOPATH` 和 `GOCACHE`
        - java: 用作 Maven local repo（`MAVEN_OPTS=-Dmaven.repo.local=...`）

        跨项目复用：相同依赖版本只下载一次，节省磁盘 + 冷启动时间。
        """
        return os.path.join(self.data_dir, "work", "lang_cache")

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
        return True

    @property
    def METRICS_USE_REDIS_CACHE(self) -> bool:
        return True

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
        """Validate mandatory PostgreSQL and Redis infrastructure."""
        database_url = self.DATABASE_URL.strip()
        if not database_url:
            raise ValueError("DATABASE_URL 必须设置，且只能使用 PostgreSQL。")
        _validate_database_url(database_url)
        redis_url = self.REDIS_URL.strip()
        if not redis_url:
            raise ValueError("REDIS_URL 必须设置。")
        _validate_redis_url(redis_url)
        if self.LOGIN_PASSWORD_ENCRYPTION_REQUIRED and not self.LOGIN_PASSWORD_ENCRYPTION_ENABLED:
            raise ValueError("LOGIN_PASSWORD_ENCRYPTION_REQUIRED 需同时启用 LOGIN_PASSWORD_ENCRYPTION_ENABLED")
        return self


# 全局配置实例
settings = Settings()
