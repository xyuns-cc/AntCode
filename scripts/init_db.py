"""Database initialization and idempotent legacy-schema upgrade entry point."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "antcode_core" / "src"))
sys.path.insert(0, str(ROOT / "services" / "web_api" / "src"))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

from scripts.init_db_environment import check_environment as _check_env  # noqa: E402
from scripts.init_db_indexes import (  # noqa: E402, F401
    PERFORMANCE_INDEXES,
    _drop_invalid_index,
    _index_is_valid,
    create_performance_indexes,
)
from scripts.init_db_legacy_schema import (  # noqa: E402, F401
    NEW_FEATURE_COLUMNS,
    WORKERS_LEGACY_COLUMNS,
    upgrade_legacy_schema,
)
from scripts.init_db_schema_upgrades import align_database_integrity  # noqa: E402
from scripts.init_db_schema_validation import validate_current_schema  # noqa: E402

REQUIRED_TABLES: tuple[str, ...] = (
    "users",
    "workers",
    "worker_heartbeats",
    "worker_events",
    "worker_install_keys",
    "worker_projects",
    "worker_project_files",
    "scheduled_tasks",
    "task_executions",
    "task_run_lease_generations",
    "task_logs",
    "projects",
    "project_files",
    "project_rules",
    "project_codes",
    "project_sources",
    "runtimes",
    "project_runtime_bindings",
    "crawl_batches",
    "audit_logs",
    "system_configs",
    "git_credentials",
    "git_repositories",
    "source_artifacts",
    "source_artifact_chunks",
    "run_source_snapshots",
    "worker_performance_history",
    "spider_metrics_history",
    "user_worker_permissions",
    "user_sessions",
    "scheduler_outbox",
    "scheduler_authority",
    "antcode_data_migrations",
)

_TABLE_EXISTS_SQL = """
SELECT 1 AS ok FROM information_schema.tables
 WHERE table_schema = 'public' AND table_name = $1 AND table_type = 'BASE TABLE'
"""


@asynccontextmanager
async def _database_lifecycle() -> AsyncIterator[None]:
    """Initialize the shared ORM and always release its connections."""
    from antcode_core.infrastructure.db.tortoise import (
        close_db,
        get_default_tortoise_config,
        init_db,
    )

    config = get_default_tortoise_config(service="web_api")
    await init_db(config=config, service="web_api")
    try:
        yield
    finally:
        await close_db()


@asynccontextmanager
async def _database_connection() -> AsyncIterator[Any]:
    from tortoise import connections

    async with _database_lifecycle():
        yield connections.get("default")


async def _generate_schemas() -> None:
    from tortoise import Tortoise

    async with _database_lifecycle():
        logger.info("生成 schema (model -> PG)...")
        await Tortoise.generate_schemas(safe=True)
    logger.info("schema 已就位（safe=True，只补缺失的表）")


async def _upgrade_legacy_schema() -> None:
    async with _database_connection() as connection:
        await upgrade_legacy_schema(connection)


async def _align_database_integrity() -> None:
    async with _database_connection() as connection:
        removed_indexes = await align_database_integrity(connection)
    for index_name in removed_indexes:
        logger.info("已删除重复 public_id 普通索引: {}", index_name)


async def _migrate_worker_credentials() -> None:
    from scripts.migrate_worker_credentials import main as migrate_worker_credentials

    await migrate_worker_credentials()


async def _check_required_tables() -> None:
    missing: list[str] = []
    async with _database_connection() as connection:
        for table in REQUIRED_TABLES:
            rows = await connection.execute_query_dict(_TABLE_EXISTS_SQL, [table])
            if not rows:
                missing.append(table)
    if missing:
        raise RuntimeError(f"必要表缺失: {missing}")
    logger.info("核心表校验通过（{} 张）", len(REQUIRED_TABLES))


async def _create_performance_indexes() -> None:
    async with _database_connection() as connection:
        await create_performance_indexes(connection, PERFORMANCE_INDEXES)


async def _validate_schema_contracts() -> None:
    async with _database_connection() as connection:
        await validate_current_schema(connection)
    logger.info("数据库 schema 类型/default/唯一性/索引谓词校验通过")


async def _init_system_config() -> None:
    from antcode_core.application.services.system_config.system_config_service import (
        system_config_service,
    )

    async with _database_lifecycle():
        await system_config_service.initialize_default_configs()
        await system_config_service.reload_config_cache()
    logger.info("默认系统配置已初始化")


async def _create_admin() -> None:
    username = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin").strip()
    password = os.environ.get("DEFAULT_ADMIN_PASSWORD", "").strip()
    if not password:
        logger.warning("未设置 DEFAULT_ADMIN_PASSWORD，跳过默认管理员创建")
        return

    from antcode_core.domain.models.user import User, UserRole

    async with _database_lifecycle():
        existing = await User.get_or_none(username=username)
        if existing:
            logger.info("管理员 {} 已存在，跳过创建", username)
            return
        user = User(
            username=username,
            email=f"{username}@localhost",
            role=UserRole.SUPER_ADMIN,
            is_admin=True,
            is_active=True,
        )
        user.set_password(password)
        await user.save()
    logger.info("默认管理员已创建: username={}", username)


async def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    await _check_env()
    logger.info("=== AntCode 数据库初始化 ===")
    logger.info("目标 DB: {}", os.environ["DATABASE_URL"].split("@")[-1])
    await _generate_schemas()
    await _upgrade_legacy_schema()
    await _align_database_integrity()
    await _migrate_worker_credentials()
    from scripts.encrypt_sensitive_data import main as encrypt_sensitive_data

    await encrypt_sensitive_data()
    await _check_required_tables()
    await _create_performance_indexes()
    await _validate_schema_contracts()
    await _init_system_config()
    await _create_admin()
    logger.info("=== 初始化完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
