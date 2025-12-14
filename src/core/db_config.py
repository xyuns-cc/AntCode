"""
数据库配置模块 - Aerich 迁移专用

提供 Tortoise ORM 配置，供 Aerich 迁移工具使用。
"""
import os
from functools import lru_cache
from urllib.parse import urlparse

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 基础目录
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
DATA_DIR = os.path.join(BASE_DIR, "data")


def get_database_url() -> str:
    """获取数据库 URL"""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        return db_url
    return f"sqlite:///{os.path.join(DATA_DIR, 'db', 'antcode.sqlite3')}"


def _parse_db_url(db_url: str) -> dict:
    """解析数据库 URL"""
    parsed = urlparse(db_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or (3306 if "mysql" in db_url.lower() else 5432),
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "database": (parsed.path.lstrip("/").split("?")[0]) or "antcode",
    }


@lru_cache()
def get_tortoise_config() -> dict:
    """
    获取 Tortoise ORM 配置
    
    支持的数据库类型：
    - SQLite: sqlite:///path/to/db.sqlite3
    - MySQL: mysql://user:pass@host:port/dbname
    - PostgreSQL: postgres://user:pass@host:port/dbname
    """
    db_url = get_database_url()
    db_url_lower = db_url.lower()

    if db_url_lower.startswith("sqlite"):
        file_path = db_url.replace("sqlite:///", "")
        if not file_path.startswith("/"):
            file_path = os.path.join(DATA_DIR, "db", os.path.basename(file_path))
        connection_config = {
            "engine": "tortoise.backends.sqlite",
            "credentials": {"file_path": file_path},
        }
    elif "mysql" in db_url_lower or "mariadb" in db_url_lower:
        creds = _parse_db_url(db_url)
        connection_config = {
            "engine": "tortoise.backends.mysql",
            "credentials": {
                "host": creds["host"],
                "port": creds["port"],
                "user": creds["user"],
                "password": creds["password"],
                "database": creds["database"],
                "charset": "utf8mb4",
            },
        }
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
        }
    else:
        raise ValueError(f"不支持的数据库类型: {db_url}")

    return {
        "connections": {"default": connection_config},
        "apps": {
            "models": {
                "models": ["src.models", "aerich.models"],
                "default_connection": "default",
            },
        },
        "use_tz": False,
        "timezone": "Asia/Shanghai",
    }


# Aerich 使用的配置变量
TORTOISE_ORM = get_tortoise_config()
