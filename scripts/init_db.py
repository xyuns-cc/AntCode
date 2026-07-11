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
    (
        "idx_task_executions_crawl_batch_status",
        """
        CREATE INDEX IF NOT EXISTS "idx_task_executions_crawl_batch_status"
            ON "task_executions" (("result_data"->>'crawl_batch_id'), "status")
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
    # P1-01: task_logs.event_id 的**部分唯一索引**。
    # LogIngestLoop 走 ``INSERT ... ON CONFLICT ("event_id") WHERE
    # "event_id" IS NOT NULL DO NOTHING``，PG 的 ON CONFLICT 推断需要索引
    # 谓词与 INSERT WHERE 完全一致；用普通 UNIQUE 会无法命中推断。
    # Tortoise 不支持声明部分唯一索引，故此处显式建。
    (
        "idx_task_logs_event_id_unique",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS "idx_task_logs_event_id_unique"
            ON "task_logs" ("event_id")
            WHERE "event_id" IS NOT NULL
        """,
    ),
]

# ---------------------------------------------------------------------------
# 旧库升级补丁（幂等）
# ---------------------------------------------------------------------------
# ``generate_schemas(safe=True)`` 只创建**不存在的表**，不会给已存在的表加列。
# 全新部署无感；但如果 DATABASE_URL 指向老版本建过的库（例如压测遗留库），
# workers 表就缺 P1-10 新增的凭证 hash 列，启动后
# ``Worker.filter(api_key_hash=...)`` 会直接 SQL 报错（UndefinedColumn）。
# 这里按 information_schema 探测，缺列才 ALTER，可重复执行。
# 列定义与 migrations/models/20260710_secure_worker_credentials.sql 保持一致。
WORKERS_LEGACY_COLUMNS: list[tuple[str, str]] = [
    (
        "api_key_hash",
        'ALTER TABLE "workers" ADD COLUMN IF NOT EXISTS "api_key_hash" VARCHAR(128) NULL',
    ),
    (
        "secret_key_hash",
        'ALTER TABLE "workers" ADD COLUMN IF NOT EXISTS "secret_key_hash" VARCHAR(128) NULL',
    ),
    (
        "secret_key_encrypted",
        'ALTER TABLE "workers" ADD COLUMN IF NOT EXISTS "secret_key_encrypted" TEXT NULL',
    ),
    (
        "api_key_previous_hash",
        'ALTER TABLE "workers" ADD COLUMN IF NOT EXISTS "api_key_previous_hash" VARCHAR(128) NULL',
    ),
]

# workers 凭证 hash 列的查询索引（model 里 db_index=True，认证按 hash 查询）。
# 只有当该列上**一个索引都没有**时才补建 —— 全新库 Tortoise 已按自己的命名
# 建过索引，重复建第二个纯属浪费。
WORKERS_LEGACY_INDEXES: list[tuple[str, str, str]] = [
    (
        "api_key_hash",
        "idx_workers_api_key_hash",
        'CREATE INDEX IF NOT EXISTS "idx_workers_api_key_hash" ON "workers" ("api_key_hash")',
    ),
    (
        "api_key_previous_hash",
        "idx_workers_api_key_previous_hash",
        'CREATE INDEX IF NOT EXISTS "idx_workers_api_key_previous_hash" ON "workers" ("api_key_previous_hash")',
    ),
]

# P1-01: 启动完成后必须存在的核心表清单。缺任何一张都属于灾难性 model
# 漏声明（例如迁移 37 add_task_logs 消失后 task_logs 表不建的历史故障），
# 必须在部署脚本里立刻 fail-fast，不能让服务带着"空日志页"上线。
REQUIRED_TABLES: list[str] = [
    "users",
    "workers",
    "worker_heartbeats",
    "worker_events",
    "worker_install_keys",
    "scheduled_tasks",
    "task_executions",
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


async def _check_required_tables() -> None:
    """校验核心表全部落地；缺一即 fail-fast。

    历史 P1-01：迁移 37 add_task_logs 被删后没有对应 model，
    ``generate_schemas`` 不建 task_logs 表，服务照常启动但日志页永远空。
    这里在建表后立即 SELECT information_schema，让部署脚本 exit 1。
    """
    from antcode_core.infrastructure.db.tortoise import (
        close_db,
        get_default_tortoise_config,
        init_db,
    )
    from tortoise import Tortoise

    config = get_default_tortoise_config(service="web_api")
    await init_db(config=config, service="web_api")
    conn = Tortoise.get_connection("default")
    missing: list[str] = []
    for tbl in REQUIRED_TABLES:
        rows = await conn.execute_query_dict(
            "SELECT 1 AS ok FROM information_schema.tables WHERE table_schema = 'public' AND table_name = $1",
            [tbl],
        )
        if not rows:
            missing.append(tbl)
    await close_db()
    if missing:
        logger.error(
            "必要表未创建: {}. 请检查 antcode_core.domain.models 是否声明了对应 model，或补写 SQL 迁移。",
            ", ".join(missing),
        )
        raise RuntimeError(f"必要表缺失: {missing}")
    logger.info("核心表校验通过（{} 张）", len(REQUIRED_TABLES))


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


async def _upgrade_legacy_schema() -> None:
    """老库（非全新部署）结构对齐 —— 幂等，可重复执行。

    ``generate_schemas(safe=True)`` 只补缺失的表，不改已存在的表。本函数
    覆盖两类"老库跑新代码"的断裂点：

    1. **workers 凭证 hash 列**（P1-10）：老库缺 api_key_hash /
       secret_key_hash / secret_key_encrypted / api_key_previous_hash，
       认证查询 ``Worker.filter(api_key_hash=...)`` 会直接 UndefinedColumn。
       按列探测补 ALTER + 查询索引。
    2. **project_sources.entry_point**：新 model 已删除该字段，但老库上它
       是 NOT NULL —— 新代码 INSERT project_sources 不再写这列，会违反
       约束。此处只 ``DROP NOT NULL``（非破坏性）；要彻底删列请手动执行
       migrations/models/20260711_remove_project_source_mirrors.sql。

    全新库上以上探测全部命中"已就位/列不存在"，本函数是纯 no-op。
    """
    from antcode_core.infrastructure.db.tortoise import (
        close_db,
        get_default_tortoise_config,
        init_db,
    )
    from tortoise import Tortoise

    config = get_default_tortoise_config(service="web_api")
    await init_db(config=config, service="web_api")
    conn = Tortoise.get_connection("default")

    async def _column_exists(table: str, column: str) -> bool:
        rows = await conn.execute_query_dict(
            "SELECT 1 AS ok FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
            [table, column],
        )
        return bool(rows)

    # 1. workers 凭证 hash 列（generate_schemas 建过 workers 表，ALTER 一定可执行）
    added_columns = 0
    for column, ddl in WORKERS_LEGACY_COLUMNS:
        if await _column_exists("workers", column):
            continue
        await conn.execute_query(ddl)
        added_columns += 1
        logger.info("旧库补列: workers.{}", column)

    # 补 hash 查询索引 —— 仅当该列上没有任何索引时（全新库 Tortoise 自带）
    for column, index_name, ddl in WORKERS_LEGACY_INDEXES:
        rows = await conn.execute_query_dict(
            """
            SELECT 1 AS ok
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY (i.indkey)
            WHERE n.nspname = 'public' AND t.relname = 'workers' AND a.attname = $1
            """,
            [column],
        )
        if rows:
            continue
        await conn.execute_query(ddl)
        logger.info("旧库补索引: {}", index_name)

    if added_columns:
        logger.warning(
            "workers 表补了 {} 个凭证 hash 列（老库升级路径）。"
            "存量 Worker 的明文凭证需要回填 hash 才能继续认证，"
            "请执行 `uv run python scripts/migrate_worker_credentials.py`（见 "
            "migrations/models/20260710_secure_worker_credentials.sql 注释）。",
            added_columns,
        )

    # 2. project_sources.entry_point：新 model 已删；老库上放开 NOT NULL
    rows = await conn.execute_query_dict(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
        ["project_sources", "entry_point"],
    )
    if rows and rows[0]["is_nullable"] == "NO":
        await conn.execute_query('ALTER TABLE "project_sources" ALTER COLUMN "entry_point" DROP NOT NULL')
        logger.warning(
            "旧库 project_sources.entry_point 已放开 NOT NULL（新版本不再写该列）。"
            "彻底删列请手动执行 migrations/models/20260711_remove_project_source_mirrors.sql"
        )

    await close_db()
    logger.info("旧库结构对齐检查完成")


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
        logger.warning("未设置 DEFAULT_ADMIN_PASSWORD —— 跳过默认管理员创建。上线前请在 .env 里显式配置。")
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
    # generate_schemas 只建缺表；老库跑新代码时还要补新加的列（见函数 docstring）
    await _upgrade_legacy_schema()
    await _check_required_tables()
    await _create_performance_indexes()
    await _init_system_config()
    await _create_admin()
    logger.info("=== 初始化完成 ✓ ===")
    logger.info("下一步：启动 web_api / master / worker，见 README.md")


if __name__ == "__main__":
    asyncio.run(main())
