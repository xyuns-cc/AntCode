"""数据库一键初始化脚本 —— 全新部署首次执行。

流程：
1. 校验 DATABASE_URL / ENCRYPTION_KEY / JWT_SECRET 环境变量
2. Tortoise.generate_schemas() 从 model 定义直接建全部表 + 主键索引
3. 补建 model 里没声明但性能关键的索引（JSONB 函数索引、复合索引）
4. 初始化默认系统配置
5. 创建默认管理员账号（DEFAULT_ADMIN_USERNAME / DEFAULT_ADMIN_PASSWORD）

**使用**：

    uv run python scripts/init_db.py

**幂等性**：脚本可以重复运行。`generate_schemas(safe=True)` 只建缺失的
表，CREATE INDEX IF NOT EXISTS 只补缺失的索引；管理员账号 get_or_create
不重复。

**为什么放弃了 aerich 迁移链**：这是全新项目发版，没有历史包袱；model
定义是唯一的真源，一次性建表比维护 40+ 个迁移文件更清晰。日后 model
schema 变更请走 aerich (`uv run aerich init && uv run aerich migrate`)
或者直接手写 SQL 补丁并记录在 `docs/database-setup.md` 里。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 确保 packages/services 在 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "antcode_core" / "src"))
sys.path.insert(0, str(ROOT / "services" / "web_api" / "src"))

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402


# T7 阶段新加的性能索引 —— 全部 IF NOT EXISTS 幂等
PERFORMANCE_INDEXES: list[tuple[str, str]] = [
    # T7-B1a (P0-1): task_executions JSONB 函数索引，
    # crawl_batch_status_loop / test_service / web_api 全部依赖这条
    (
        "idx_task_executions_crawl_batch_id",
        """
        CREATE INDEX IF NOT EXISTS "idx_task_executions_crawl_batch_id"
            ON "task_executions" (("result_data"->>'crawl_batch_id'))
            WHERE "result_data"->>'crawl_batch_id' IS NOT NULL
        """,
    ),
    # T7-B1c (P2-1): crawl_batches 状态列表 + 时间排序
    (
        "idx_crawl_batches_status_created",
        """
        CREATE INDEX IF NOT EXISTS "idx_crawl_batches_status_created"
            ON "crawl_batches" ("status", "created_at" DESC)
        """,
    ),
]


async def _check_env() -> None:
    required = ["DATABASE_URL", "ENCRYPTION_KEY", "JWT_SECRET"]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        logger.error(
            "环境变量缺失: {}。请检查 .env 或环境是否加载。",
            ", ".join(missing),
        )
        sys.exit(1)


async def _generate_schemas() -> None:
    from antcode_core.infrastructure.db.tortoise import (
        close_db,
        get_default_tortoise_config,
        init_db,
    )
    from tortoise import Tortoise

    config = get_default_tortoise_config(service="web_api")
    await init_db(config=config, service="web_api")
    logger.info("生成 schema (model → PG)…")
    await Tortoise.generate_schemas(safe=True)
    logger.info("schema 已就位（safe=True，只补缺失的表）")
    await close_db()


async def _create_performance_indexes() -> None:
    from antcode_core.infrastructure.db.tortoise import (
        close_db,
        get_default_tortoise_config,
        init_db,
    )
    from tortoise import connections

    config = get_default_tortoise_config(service="web_api")
    await init_db(config=config, service="web_api")
    conn = connections.get("default")
    for name, sql in PERFORMANCE_INDEXES:
        try:
            await conn.execute_query(sql)
            logger.info("索引 OK: {}", name)
        except Exception as exc:
            logger.warning("索引 {} 建立失败（可能已存在）: {}", name, exc)
    await close_db()


async def _init_system_config() -> None:
    from antcode_core.application.services.system_config.system_config_service import (
        system_config_service,
    )
    from antcode_core.infrastructure.db.tortoise import (
        close_db,
        get_default_tortoise_config,
        init_db,
    )

    config = get_default_tortoise_config(service="web_api")
    await init_db(config=config, service="web_api")
    try:
        await system_config_service.initialize_default_configs()
        await system_config_service.reload_config_cache()
        logger.info("默认系统配置已初始化")
    except Exception as exc:
        logger.warning("初始化系统配置失败（可忽略并继续）: {}", exc)
    await close_db()


async def _create_admin() -> None:
    username = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin").strip()
    password = os.environ.get("DEFAULT_ADMIN_PASSWORD", "").strip()
    if not password:
        logger.warning(
            "未设置 DEFAULT_ADMIN_PASSWORD —— 跳过默认管理员创建。"
            "上线前请在 .env 里显式配置。"
        )
        return

    from antcode_core.domain.models.enums import UserRole
    from antcode_core.domain.models.user import User
    from antcode_core.infrastructure.db.tortoise import (
        close_db,
        get_default_tortoise_config,
        init_db,
    )

    config = get_default_tortoise_config(service="web_api")
    await init_db(config=config, service="web_api")

    existing = await User.get_or_none(username=username)
    if existing:
        logger.info("管理员 {} 已存在，跳过创建", username)
    else:
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
    await close_db()


async def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    await _check_env()
    logger.info("=== AntCode 数据库初始化 ===")
    logger.info("目标 DB: {}", os.environ["DATABASE_URL"].split("@")[-1])
    await _generate_schemas()
    await _create_performance_indexes()
    await _init_system_config()
    await _create_admin()
    logger.info("=== 初始化完成 ✓ ===")
    logger.info("下一步：启动 web_api / master / worker，见 README.md")


if __name__ == "__main__":
    asyncio.run(main())
