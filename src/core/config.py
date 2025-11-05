# src/core/config.py (修改部分)
import os
import typing

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置类"""

    # 应用基本信息
    APP_NAME: str = "AntCode API"
    APP_DESCRIPTION: str = "AntCode API"
    APP_VERSION: str = "1.3.0"
    DEBUG: bool = True

    # CORS配置
    CORS_ORIGINS: typing.List[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: typing.List[str] = ["*"]
    CORS_ALLOW_HEADERS: typing.List[str] = ["*"]

    # JWT配置
    JWT_SECRET_KEY: str = Field(
        default="a82d05e052e1be881550dd66c406bbf351906249a68f2345ce9a8c86081f146f",
        description="JWT密钥"
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24小时

    # 项目路径配置
    PROJECT_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    BASE_DIR: str = os.path.abspath(os.path.join(PROJECT_ROOT, os.pardir))

    # 存储配置
    LOCAL_STORAGE_PATH: str = f"{BASE_DIR}/storage/projects"

    # 文件限制配置
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100MB
    ALLOWED_FILE_TYPES: typing.List[str] = [
        '.zip', '.tar.gz', '.py', '.txt', '.json', '.md', '.yml', '.yaml'
    ]

    # ============ Redis配置（修改部分）============
    # Redis连接配置 - 使用URL格式，支持密码认证
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis连接URL，格式: redis://[:password]@host:port/db"
    )
    REDIS_TASK_QUEUE: str = "spider:tasks"  # Redis任务队列名称
    REDIS_ENABLED: bool = True  # 是否启用Redis（建议改为True以支持规则任务）

    # Worker标识配置（新增）
    WORKER_ID: str = "Scraper-Node-Default"  # Worker节点标识，用于区分不同的爬虫节点

    # ============ Redis配置结束 ============

    # 默认管理员配置
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = Field(
        default="admin",
        description="默认管理员密码（首次启动时创建）"
    )

    # ============ 调度器配置 ============
    # 任务调度配置
    SCHEDULER_TIMEZONE: str = "Asia/Shanghai"  # 调度器时区
    MAX_CONCURRENT_TASKS: int = 10  # 最大并发任务数

    # 任务执行配置
    TASK_EXECUTION_TIMEOUT: int = 3600  # 默认任务超时时间（秒）
    TASK_MAX_RETRIES: int = 3  # 任务最大重试次数
    TASK_RETRY_DELAY: int = 60  # 重试延迟（秒）
    TASK_LOG_RETENTION_DAYS: int = 30  # 日志保留天数



    # 任务存储配置
    TASK_LOG_DIR: str = f"{BASE_DIR}/logs/tasks"  # 任务日志存储目录
    TASK_LOG_MAX_SIZE: int = 100 * 1024 * 1024  # 单个日志文件最大大小（100MB）
    TASK_EXECUTION_WORK_DIR: str = f"{BASE_DIR}/storage/executions"  # 任务执行工作目录
    
    # 解释器/虚拟环境存储配置
    VENV_STORAGE_ROOT: str = f"{BASE_DIR}/storage/venvs"  # 虚拟环境根目录
    MISE_DATA_ROOT: str = f"{BASE_DIR}/storage/mise"      # mise 数据根目录（用于下载解释器）
    
    # 清理配置
    CLEANUP_WORKSPACE_ON_COMPLETION: bool = True  # 任务完成后是否立即清理工作目录
    CLEANUP_WORKSPACE_MAX_AGE_HOURS: int = 24  # 工作目录最大保留时间（小时）

    # ============ 调度器配置结束 ============

    # ============ 统一缓存配置 ============
    # 全局缓存开关
    CACHE_ENABLED: bool = Field(
        default=True,
        description="是否启用缓存系统"
    )
    
    # 统一缓存类型配置 - 核心配置项
    CACHE_TYPE: str = Field(
        default="redis",
        description="缓存类型：redis（使用Redis缓存）或 memory（使用内存缓存）"
    )
    
    # 缓存策略配置
    CACHE_FALLBACK_TO_MEMORY: bool = Field(
        default=True,
        description="是否启用内存缓存作为备份（双层缓存模式）"
    )
    
    # 缓存时间配置（秒）
    CACHE_DEFAULT_TTL: int = Field(
        default=300,
        description="默认缓存时间（秒）"
    )
    
    # 特定模块缓存时间配置
    METRICS_CACHE_TTL: int = Field(
        default=30,
        description="系统指标缓存时间（秒）"
    )
    USERS_CACHE_TTL: int = Field(
        default=300,
        description="用户列表缓存时间（秒）"
    )
    API_CACHE_TTL: int = Field(
        default=300,
        description="API响应缓存时间（秒）"
    )
    QUERY_CACHE_TTL: int = Field(
        default=300,
        description="数据库查询缓存时间（秒）"
    )
    
    # 系统指标后台更新配置
    METRICS_BACKGROUND_UPDATE: bool = Field(
        default=True,
        description="是否启用系统指标后台更新"
    )
    METRICS_UPDATE_INTERVAL: int = Field(
        default=15,
        description="系统指标后台更新间隔（秒）"
    )
    
    @property
    def CACHE_USE_REDIS(self):
        """根据CACHE_TYPE返回是否使用Redis"""
        return self.CACHE_TYPE.lower() == "redis"
    
    # 向后兼容的属性（保持原有代码可用）
    @property
    def METRICS_USE_REDIS_CACHE(self):
        return self.CACHE_USE_REDIS
    
    @property 
    def USERS_USE_REDIS_CACHE(self):
        return self.CACHE_USE_REDIS

    # 数据库配置
    DATABASE_URL: str = Field(
        default="sqlite:///./antcode.sqlite3",
        description="数据库连接URL"
    )
    DATABASE_MIN_CONNECTIONS: int = Field(
        default=5,
        description="数据库连接池最小连接数"
    )
    DATABASE_MAX_CONNECTIONS: int = Field(
        default=20,
        description="数据库连接池最大连接数"
    )
    
    # Tortoise ORM配置
    @property
    def TORTOISE_ORM(self):
        """Tortoise ORM配置 - 使用属性方法避免循环依赖"""
        return {
            "connections": {
                "default": {
                    "engine": "tortoise.backends.sqlite",
                    "credentials": {
                        "file_path": f"{self.BASE_DIR}/antcode.sqlite3"
                    },
                    "minsize": self.DATABASE_MIN_CONNECTIONS,
                    "maxsize": self.DATABASE_MAX_CONNECTIONS,
                },
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

    class Config:
        env_file = ".env"
        case_sensitive = True  # 添加大小写敏感配置


settings = Settings()
