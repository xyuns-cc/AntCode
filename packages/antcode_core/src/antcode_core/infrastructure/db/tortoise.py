"""PostgreSQL-only Tortoise ORM configuration."""

import os
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from loguru import logger

# 加载环境变量
load_dotenv()


def get_database_url() -> str:
    """获取数据库 URL"""
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise ValueError("DATABASE_URL 必须设置，且只能使用 PostgreSQL。")
    if not db_url.lower().startswith(("postgres://", "postgresql://")):
        raise ValueError("DATABASE_URL 只能使用 PostgreSQL 连接串。")
    return db_url


def _parse_db_url(db_url: str) -> dict[str, Any]:
    """解析数据库 URL"""
    parsed = urlparse(db_url)
    database = parsed.path.lstrip("/").split("?")[0]
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("DATABASE_URL port 无效。") from exc

    required_parts = {
        "host": parsed.hostname,
        "port": port,
        "user": parsed.username,
        "password": parsed.password,
        "database": database,
    }
    missing = [name for name, value in required_parts.items() if value in (None, "")]
    if missing:
        raise ValueError(f"DATABASE_URL 缺少 {', '.join(missing)}。")

    return {
        "host": parsed.hostname,
        "port": port,
        "user": parsed.username,
        "password": parsed.password,
        "database": database,
    }


def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _resolve_pool_size(
    service: str | None,
    min_connections: int | None,
    max_connections: int | None,
) -> tuple[int, int]:
    """从 Settings 解析连接池大小，显式参数优先于配置。"""
    from antcode_core.common.config import settings

    resolved_min = min_connections if min_connections is not None else settings.DB_POOL_MIN
    resolved_max = max_connections if max_connections is not None else settings.db_pool_max_for(service)
    if resolved_min > resolved_max:
        resolved_min = resolved_max
    return resolved_min, resolved_max


def get_tortoise_config(
    models_module: str = "antcode_core.domain.models",
    min_connections: int | None = None,
    max_connections: int | None = None,
    service: str | None = None,
) -> dict[str, Any]:
    """获取 Tortoise ORM 配置

    Args:
        models_module: 模型模块路径
        min_connections: 最小连接数；None 时取 Settings.DB_POOL_MIN
        max_connections: 最大连接数；None 时按 service 取 Settings.DB_POOL_MAX_*
        service: 服务名（web_api/master/gateway/worker），用于选择 maxsize 默认值

    Returns:
        Tortoise ORM 配置字典
    """
    db_url = get_database_url()
    creds = _parse_db_url(db_url)
    pool_min, pool_max = _resolve_pool_size(service, min_connections, max_connections)
    connection_config = {
        "engine": "tortoise.backends.asyncpg",
        "credentials": {
            "host": creds["host"],
            "port": creds["port"],
            "user": creds["user"],
            "password": creds["password"],
            "database": creds["database"],
        },
        "minsize": pool_min,
        "maxsize": pool_max,
    }

    return {
        "connections": {"default": connection_config},
        "apps": {
            "models": {
                "models": [models_module],
                "default_connection": "default",
            },
        },
        "use_tz": False,
        "timezone": "Asia/Shanghai",
    }


@lru_cache
def _cached_config(service: str | None) -> dict[str, Any]:
    return get_tortoise_config(service=service)


def get_default_tortoise_config(service: str | None = None) -> dict[str, Any]:
    """获取默认的 Tortoise ORM 配置（带缓存，按 service 名分键）"""
    return _cached_config(service)


async def _warn_if_pool_oversized(service: str | None) -> None:
    """启动后校验 PG max_connections 是否足以支撑当前 pool。"""
    from tortoise import Tortoise

    from antcode_core.common.config import settings

    pool_max = settings.db_pool_max_for(service)
    try:
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT setting::int AS max_conn FROM pg_settings WHERE name='max_connections'"
        )
    except Exception as exc:
        logger.debug(f"读取 PG max_connections 失败，跳过校验: {exc}")
        return
    if not rows:
        return
    pg_max = int(rows[0]["max_conn"])
    if pool_max * 2 > pg_max:
        logger.warning(
            "服务 {} 的 DB 连接池 maxsize={} 已超过 PG max_connections={} 的一半，"
            "多实例部署可能触发 'too many connections'。建议调高 PG max_connections 或下调 DB_POOL_MAX_{}。",
            service or "default",
            pool_max,
            pg_max,
            (service or "default").upper(),
        )


async def init_db(
    config: dict[str, Any] | None = None,
    service: str | None = None,
) -> None:
    """初始化数据库连接

    Args:
        config: Tortoise ORM 配置；为 None 时按 service 取默认
        service: 服务名（web_api/master/gateway/worker），用于选择 maxsize
    """
    from tortoise import Tortoise

    if config is None:
        config = get_default_tortoise_config(service=service)

    await Tortoise.init(config=config)
    logger.info("数据库连接已初始化 (service={})", service or "default")
    await _warn_if_pool_oversized(service)


async def close_db() -> None:
    """关闭数据库连接"""
    from tortoise import Tortoise

    await Tortoise.close_connections()
    logger.info("数据库连接已关闭")
