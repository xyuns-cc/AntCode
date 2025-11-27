import os

from pydantic import Field
from pydantic_settings import BaseSettings


def _default_frontend_port():
    frontend_port = os.getenv("FRONTEND_PORT", "").strip()
    if frontend_port:
        try:
            return int(frontend_port)
        except ValueError:
            pass
    vite_port = os.getenv("VITE_PORT", "").strip()
    if vite_port:
        try:
            return int(vite_port)
        except ValueError:
            pass
    return 3000


class Settings(BaseSettings):
    """应用配置"""

    # 数据库配置（留空使用默认 SQLite）
    DATABASE_URL: str = Field(default="")
    REDIS_URL: str = Field(default="")
    SERVER_HOST: str = Field(default="0.0.0.0")
    SERVER_PORT: int = Field(default=8000)
    FRONTEND_PORT: int = Field(default_factory=_default_frontend_port)
    SERVER_DOMAIN: str = Field(default="localhost")
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: str = Field(default="text")
    LOG_TO_FILE: bool = Field(default=True)

    APP_NAME: str = "AntCode API"
    APP_DESCRIPTION: str = "AntCode Task Scheduling Platform"
    APP_VERSION: str = "1.3.0"
    DEBUG: bool = False

    @property
    def JWT_SECRET_KEY(self):
        from src.core.auth import jwt_secret_manager
        return jwt_secret_manager.get_secret()
    
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    @property
    def CORS_ORIGINS(self):
        origins = [
            f"http://{self.SERVER_DOMAIN}:{self.FRONTEND_PORT}",
            f"http://localhost:{self.FRONTEND_PORT}",
            f"http://127.0.0.1:{self.FRONTEND_PORT}",
        ]
        if self.SERVER_DOMAIN not in ["localhost", "127.0.0.1"]:
            origins.append(f"https://{self.SERVER_DOMAIN}")
        return origins
    
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list = ["*"]
    CORS_ALLOW_HEADERS: list = ["*"]
    
    @property
    def HOST(self):
        return self.SERVER_HOST
    
    @property
    def PORT(self):
        return self.SERVER_PORT

    # 数据库配置（留空使用默认 SQLite）
    DATABASE_URL: str = Field(default="")
    
    # 项目根目录
    BASE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
    
    @property
    def data_dir(self):
        """数据目录：所有生成文件的根目录"""
        return os.path.join(self.BASE_DIR, "data")
    
    @property
    def db_url(self):
        """实际使用的数据库 URL"""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"sqlite:///{os.path.join(self.data_dir, 'db', 'antcode.sqlite3')}"
    
    @property
    def LOG_FILE_PATH(self):
        return os.path.join(self.data_dir, "logs", "app.log")
    
    @property
    def LOCAL_STORAGE_PATH(self):
        return os.path.join(self.data_dir, "storage", "projects")

    MAX_FILE_SIZE: int = 100 * 1024 * 1024
    MAX_FILE_EDIT_SIZE: int = 1 * 1024 * 1024
    ALLOWED_FILE_TYPES: list = ['.zip', '.tar.gz', '.py', '.txt', '.json', '.md', '.yml', '.yaml']

    REDIS_TASK_QUEUE: str = "spider:tasks"
    REDIS_ENABLED: bool = True
    WORKER_ID: str = "AntCode-Worker-Default"

    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "admin"

    SCHEDULER_TIMEZONE: str = "Asia/Shanghai"
    MAX_CONCURRENT_TASKS: int = 10
    TASK_EXECUTION_TIMEOUT: int = 3600
    TASK_MAX_RETRIES: int = 3
    TASK_RETRY_DELAY: int = 60
    TASK_LOG_RETENTION_DAYS: int = 30
    TASK_LOG_MAX_SIZE: int = 100 * 1024 * 1024
    
    @property
    def TASK_LOG_DIR(self):
        return os.path.join(self.data_dir, "logs", "tasks")
    
    @property
    def TASK_EXECUTION_WORK_DIR(self):
        return os.path.join(self.data_dir, "storage", "executions")
    
    @property
    def VENV_STORAGE_ROOT(self):
        return os.path.join(self.data_dir, "storage", "venvs")
    
    @property
    def MISE_DATA_ROOT(self):
        return os.path.join(self.data_dir, "storage", "mise")
    
    CLEANUP_WORKSPACE_ON_COMPLETION: bool = True
    CLEANUP_WORKSPACE_MAX_AGE_HOURS: int = 24

    CACHE_ENABLED: bool = True
    CACHE_DEFAULT_TTL: int = 300
    METRICS_CACHE_TTL: int = 30
    USERS_CACHE_TTL: int = 300
    API_CACHE_TTL: int = 300
    QUERY_CACHE_TTL: int = 300
    METRICS_BACKGROUND_UPDATE: bool = True
    METRICS_UPDATE_INTERVAL: int = 15
    
    @property
    def CACHE_USE_REDIS(self):
        return bool(self.REDIS_URL and self.REDIS_URL.strip())
    
    @property
    def METRICS_USE_REDIS_CACHE(self):
        return self.CACHE_USE_REDIS
    
    @property 
    def USERS_USE_REDIS_CACHE(self):
        return self.CACHE_USE_REDIS

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

    DATABASE_MIN_CONNECTIONS: int = 10
    DATABASE_MAX_CONNECTIONS: int = 50
    
    @property
    def TORTOISE_ORM(self):
        db_url = self.db_url.lower()
        
        if db_url.startswith('sqlite'):
            file_path = self.db_url.replace('sqlite:///', '')
            if not file_path.startswith('/'):
                file_path = os.path.join(self.data_dir, "db", os.path.basename(file_path))
            
            connection_config = {
                "engine": "tortoise.backends.sqlite",
                "credentials": {"file_path": file_path},
            }
        elif 'mysql' in db_url or 'mariadb' in db_url:
            connection_config = {
                "engine": "tortoise.backends.mysql",
                "credentials": {
                    "host": self._parse_db_host(),
                    "port": self._parse_db_port(),
                    "user": self._parse_db_user(),
                    "password": self._parse_db_password(),
                    "database": self._parse_db_name(),
                    "charset": "utf8mb4",
                    "connect_timeout": 120,
                },
                "minsize": self.DATABASE_MIN_CONNECTIONS,
                "maxsize": self.DATABASE_MAX_CONNECTIONS,
                "pool_recycle": 3600,
            }
        elif 'postgres' in db_url:
            connection_config = {
                "engine": "tortoise.backends.asyncpg",
                "credentials": {
                    "host": self._parse_db_host(),
                    "port": self._parse_db_port(),
                    "user": self._parse_db_user(),
                    "password": self._parse_db_password(),
                    "database": self._parse_db_name(),
                },
                "minsize": self.DATABASE_MIN_CONNECTIONS,
                "maxsize": self.DATABASE_MAX_CONNECTIONS,
            }
        else:
            raise ValueError(f"Unsupported database: {self.db_url}")
        
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
    
    def _parse_db_host(self):
        from urllib.parse import urlparse
        return urlparse(self.db_url).hostname or 'localhost'
    
    def _parse_db_port(self):
        from urllib.parse import urlparse
        port = urlparse(self.db_url).port
        return port or (3306 if 'mysql' in self.db_url.lower() else 5432)
    
    def _parse_db_user(self):
        from urllib.parse import urlparse
        return urlparse(self.db_url).username or 'root'
    
    def _parse_db_password(self):
        from urllib.parse import urlparse
        return urlparse(self.db_url).password or ''
    
    def _parse_db_name(self):
        from urllib.parse import urlparse
        db_name = urlparse(self.db_url).path.lstrip('/')
        return db_name.split('?')[0] if '?' in db_name else db_name or 'antcode'

    class Config:
        env_file = os.path.join(
            os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)),
            ".env"
        )
        case_sensitive = True
        extra = "ignore"


settings = Settings()
