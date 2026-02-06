"""
数据库配置模块

提供 Tortoise ORM 配置，支持 SQLite、MySQL、PostgreSQL。
"""

import os
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from loguru import logger

# 加载环境变量
load_dotenv()


def _find_project_root() -> str:
    """查找项目根目录"""
    from pathlib import Path

    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return str(parent)
    return str(current.parent.parent.parent.parent.parent.parent)


# 基础目录
BASE_DIR = _find_project_root()
DATA_DIR = os.path.join(BASE_DIR, "data")


def get_database_url() -> str:
    """获取数据库 URL"""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        return db_url
    return f"sqlite:///{os.path.join(DATA_DIR, 'db', 'antcode.sqlite3')}"


def _parse_db_url(db_url: str) -> dict[str, Any]:
    """解析数据库 URL"""
    parsed = urlparse(db_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or (3306 if "mysql" in db_url.lower() else 5432),
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "database": (parsed.path.lstrip("/").split("?")[0]) or "antcode",
    }

def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def get_tortoise_config(
    models_module: str = "antcode_core.domain.models",
    include_aerich: bool = False,
    min_connections: int = 20,
    max_connections: int = 200,
) -> dict[str, Any]:
    """获取 Tortoise ORM 配置

    支持的数据库类型：
    - SQLite: sqlite:///path/to/db.sqlite3
    - MySQL: mysql://user:pass@host:port/dbname
    - PostgreSQL: postgres://user:pass@host:port/dbname

    Args:
        models_module: 模型模块路径
        include_aerich: 是否包含 aerich 模型（用于迁移）
        min_connections: 最小连接数
        max_connections: 最大连接数

    Returns:
        Tortoise ORM 配置字典
    """
    db_url = get_database_url()
    db_url_lower = db_url.lower()

    if db_url_lower.startswith("sqlite"):
        file_path = db_url.replace("sqlite:///", "")
        if not file_path.startswith("/"):
            file_path = os.path.join(DATA_DIR, "db", os.path.basename(file_path))

        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        connection_config = {
            "engine": "tortoise.backends.sqlite",
            "credentials": {"file_path": file_path},
        }
    elif "mysql" in db_url_lower or "mariadb" in db_url_lower:
        creds = _parse_db_url(db_url)
        pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "300"))
        ssl_enabled = _parse_bool_env("DB_SSL", False)
        ssl_config: dict[str, Any] | None = None
        if ssl_enabled:
            ssl_config = {}
            ssl_ca = os.getenv("DB_SSL_CA")
            ssl_cert = os.getenv("DB_SSL_CERT")
            ssl_key = os.getenv("DB_SSL_KEY")
            ssl_verify = os.getenv("DB_SSL_VERIFY")
            if ssl_ca:
                ssl_config["ca"] = ssl_ca
            if ssl_cert:
                ssl_config["cert"] = ssl_cert
            if ssl_key:
                ssl_config["key"] = ssl_key
            if ssl_verify is not None and ssl_verify != "":
                ssl_config["verify_mode"] = ssl_verify
        connection_config = {
            "engine": "tortoise.backends.mysql",
            "credentials": {
                "host": creds["host"],
                "port": creds["port"],
                "user": creds["user"],
                "password": creds["password"],
                "database": creds["database"],
                "charset": "utf8mb4",
                "connect_timeout": 30,
            },
            "minsize": min_connections,
            "maxsize": max_connections,
            "pool_recycle": pool_recycle,
        }
        if ssl_enabled:
            connection_config["credentials"]["ssl"] = ssl_config or {}
    elif "postgres" in db_url_lower:
        creds = _parse_db_url(db_url)
        connection_config = {
            "engine": "tortoise.backends.asyncpg",
            "credentials": {
                "host": creds["host"],
                "port": creds["port"],
                "user": creds["user"],
                "password": creds["password"],
                "database": creds["database"],
            },
            "minsize": min_connections,
            "maxsize": max_connections,
        }
    else:
        raise ValueError(f"不支持的数据库类型: {db_url}")

    # 构建模型列表
    models_list = [models_module]
    if include_aerich:
        models_list.append("aerich.models")

    return {
        "connections": {"default": connection_config},
        "apps": {
            "models": {
                "models": models_list,
                "default_connection": "default",
            },
        },
        "use_tz": False,
        "timezone": "Asia/Shanghai",
    }


@lru_cache
def get_default_tortoise_config() -> dict[str, Any]:
    """获取默认的 Tortoise ORM 配置（带缓存）"""
    return get_tortoise_config()


async def init_db(config: dict[str, Any] | None = None) -> None:
    """初始化数据库连接

    Args:
        config: Tortoise ORM 配置，为 None 时使用默认配置
    """
    from tortoise import Tortoise

    if config is None:
        config = get_default_tortoise_config()

    await Tortoise.init(config=config)
    logger.info("数据库连接已初始化")


async def close_db() -> None:
    """关闭数据库连接"""
    from tortoise import Tortoise

    await Tortoise.close_connections()
    logger.info("数据库连接已关闭")


async def generate_schemas(safe: bool = True) -> None:
    """生成数据库表结构

    Args:
        safe: 是否安全模式（不删除已存在的表）
    """
    from tortoise import Tortoise

    await Tortoise.generate_schemas(safe=safe)
    logger.info("数据库表结构已生成")


# Aerich 使用的配置变量
TORTOISE_ORM = get_tortoise_config(
    models_module="antcode_core.domain.models",
    include_aerich=True,
)
