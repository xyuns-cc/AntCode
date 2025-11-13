# src/core/config.py
import os
import typing

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
    """应用配置类 - 精简版
    
    环境变量配置：
    - DATABASE_URL: 数据库连接地址
    - REDIS_URL: Redis连接地址
    - SERVER_HOST: 后端监听地址
    - SERVER_PORT: 后端端口
    - FRONTEND_PORT: 前端端口
    - SERVER_DOMAIN: 服务器域名或IP
    - LOG_LEVEL: 日志等级
    - LOG_FORMAT: 日志格式(text/json)
    - LOG_TO_FILE: 是否保存日志文件
    - LOG_FILE_PATH: 日志文件路径
    
    其他配置均为固定值，不可通过环境变量修改
    """

    # ============ 核心配置（可通过环境变量配置）============
    
    # 数据库配置
    DATABASE_URL: str = Field(
        default="sqlite:///./antcode.sqlite3",
        description="数据库连接地址"
    )
    
    # Redis配置
    REDIS_URL: str = Field(
        default="",
        description="Redis连接地址，留空使用内存缓存"
    )
    
    # 服务器配置
    SERVER_HOST: str = Field(
        default="0.0.0.0",
        description="后端服务器监听地址"
    )
    SERVER_PORT: int = Field(
        default=8000,
        description="后端服务器端口"
    )
    FRONTEND_PORT: int = Field(
        default_factory=_default_frontend_port,
        description="前端服务器端口"
    )
    SERVER_DOMAIN: str = Field(
        default="localhost",
        description="服务器域名或IP地址"
    )
    
    # 日志配置
    LOG_LEVEL: str = Field(
        default="INFO",
        description="日志等级: DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )
    LOG_FORMAT: str = Field(
        default="text",
        description="日志格式: text 或 json"
    )
    LOG_TO_FILE: bool = Field(
        default=True,
        description="是否保存日志到文件"
    )
    LOG_FILE_PATH: str = Field(
        default="./logs/app.log",
        description="日志文件保存路径"
    )
    
    # ============ 固定配置（不可通过环境变量修改）============
    
    # 应用基本信息
    APP_NAME: str = "AntCode API"
    APP_DESCRIPTION: str = "AntCode 任务调度和项目管理平台"
    APP_VERSION: str = "1.3.0"
    DEBUG: bool = False

    # JWT配置 - 动态生成
    @property
    def JWT_SECRET_KEY(self) -> str:
        """JWT密钥 - 首次运行时自动生成"""
        from src.core.auth import jwt_secret_manager
        return jwt_secret_manager.get_secret()
    
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24小时

    # CORS配置 - 自动生成
    @property
    def CORS_ORIGINS(self) -> typing.List[str]:
        """根据配置自动生成CORS允许的源"""
        origins = [
            f"http://{self.SERVER_DOMAIN}:{self.FRONTEND_PORT}",
            f"http://localhost:{self.FRONTEND_PORT}",
            f"http://127.0.0.1:{self.FRONTEND_PORT}",
        ]
        if self.SERVER_DOMAIN not in ["localhost", "127.0.0.1"]:
            origins.append(f"https://{self.SERVER_DOMAIN}")
        return origins
    
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: typing.List[str] = ["*"]
    CORS_ALLOW_HEADERS: typing.List[str] = ["*"]
    
    # 向后兼容
    @property
    def HOST(self) -> str:
        return self.SERVER_HOST
    
    @property
    def PORT(self) -> int:
        return self.SERVER_PORT

    # 项目路径配置
    PROJECT_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    BASE_DIR: str = os.path.abspath(os.path.join(PROJECT_ROOT, os.pardir))

    # 存储配置
    LOCAL_STORAGE_PATH: str = f"{BASE_DIR}/storage/projects"

    # 文件限制配置
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100MB
    MAX_FILE_EDIT_SIZE: int = 1 * 1024 * 1024  # 1MB
    ALLOWED_FILE_TYPES: typing.List[str] = [
        '.zip', '.tar.gz', '.py', '.txt', '.json', '.md', '.yml', '.yaml'
    ]

    # Redis内部配置
    REDIS_TASK_QUEUE: str = "spider:tasks"
    REDIS_ENABLED: bool = True
    WORKER_ID: str = "AntCode-Worker-Default"

    # 默认管理员配置
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "admin"

    # 调度器配置
    SCHEDULER_TIMEZONE: str = "Asia/Shanghai"
    MAX_CONCURRENT_TASKS: int = 10
    TASK_EXECUTION_TIMEOUT: int = 3600
    TASK_MAX_RETRIES: int = 3
    TASK_RETRY_DELAY: int = 60
    TASK_LOG_RETENTION_DAYS: int = 30
    TASK_LOG_DIR: str = f"{BASE_DIR}/logs/tasks"
    TASK_LOG_MAX_SIZE: int = 100 * 1024 * 1024
    TASK_EXECUTION_WORK_DIR: str = f"{BASE_DIR}/storage/executions"
    
    # 虚拟环境配置
    VENV_STORAGE_ROOT: str = f"{BASE_DIR}/storage/venvs"
    MISE_DATA_ROOT: str = f"{BASE_DIR}/storage/mise"
    
    # 清理配置
    CLEANUP_WORKSPACE_ON_COMPLETION: bool = True
    CLEANUP_WORKSPACE_MAX_AGE_HOURS: int = 24

    # 缓存配置
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
        """根据REDIS_URL判断是否使用Redis缓存"""
        return bool(self.REDIS_URL and self.REDIS_URL.strip())
    
    @property
    def METRICS_USE_REDIS_CACHE(self):
        return self.CACHE_USE_REDIS
    
    @property 
    def USERS_USE_REDIS_CACHE(self):
        return self.CACHE_USE_REDIS

    # 数据库连接池配置
    DATABASE_MIN_CONNECTIONS: int = 5
    DATABASE_MAX_CONNECTIONS: int = 20
    
    # Tortoise ORM配置
    @property
    def TORTOISE_ORM(self):
        """Tortoise ORM配置 - 根据DATABASE_URL自动生成"""
        # 解析数据库URL
        db_url = self.DATABASE_URL.lower()
        
        if db_url.startswith('sqlite'):
            # SQLite配置
            # 从URL中提取文件路径，例如: sqlite:///./antcode.sqlite3 -> ./antcode.sqlite3
            file_path = self.DATABASE_URL.replace('sqlite:///', '')
            if not file_path.startswith('/'):
                # 相对路径，转为绝对路径
                file_path = f"{self.BASE_DIR}/{file_path}"
            
            connection_config = {
                "engine": "tortoise.backends.sqlite",
                "credentials": {
                    "file_path": file_path
                },
            }
        elif 'mysql' in db_url or 'mariadb' in db_url:
            # MySQL/MariaDB配置
            connection_config = {
                "engine": "tortoise.backends.mysql",
                "credentials": {
                    "host": self._parse_db_host(),
                    "port": self._parse_db_port(),
                    "user": self._parse_db_user(),
                    "password": self._parse_db_password(),
                    "database": self._parse_db_name(),
                    "charset": "utf8mb4",
                    "connect_timeout": 60,
                },
                "minsize": self.DATABASE_MIN_CONNECTIONS,
                "maxsize": self.DATABASE_MAX_CONNECTIONS,
            }
        elif 'postgres' in db_url:
            # PostgreSQL配置
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
            raise ValueError(f"不支持的数据库类型: {self.DATABASE_URL}")
        
        return {
            "connections": {
                "default": connection_config,
            },
            "apps": {
                "models": {
                    "models": ["src.models", "aerich.models"],
                    "default_connection": "default",
                },
            },
            "use_tz": False,
            "timezone": "Asia/Shanghai",
        }
    
    def _parse_db_host(self):
        """从DATABASE_URL解析主机名"""
        from urllib.parse import urlparse
        parsed = urlparse(self.DATABASE_URL)
        return parsed.hostname or 'localhost'
    
    def _parse_db_port(self):
        """从DATABASE_URL解析端口"""
        from urllib.parse import urlparse
        parsed = urlparse(self.DATABASE_URL)
        if parsed.port:
            return parsed.port
        # 默认端口
        if 'mysql' in self.DATABASE_URL.lower():
            return 3306
        elif 'postgres' in self.DATABASE_URL.lower():
            return 5432
        return 3306
    
    def _parse_db_user(self):
        """从DATABASE_URL解析用户名"""
        from urllib.parse import urlparse
        parsed = urlparse(self.DATABASE_URL)
        return parsed.username or 'root'
    
    def _parse_db_password(self):
        """从DATABASE_URL解析密码"""
        from urllib.parse import urlparse
        parsed = urlparse(self.DATABASE_URL)
        return parsed.password or ''
    
    def _parse_db_name(self):
        """从DATABASE_URL解析数据库名"""
        from urllib.parse import urlparse
        parsed = urlparse(self.DATABASE_URL)
        # 去掉开头的 /
        db_name = parsed.path.lstrip('/')
        # 去掉查询参数
        if '?' in db_name:
            db_name = db_name.split('?')[0]
        return db_name or 'antcode'

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
