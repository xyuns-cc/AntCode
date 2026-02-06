"""应用生命周期管理

提供生命周期上下文管理器和服务初始化/关闭函数。
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger
from tortoise import Tortoise

from antcode_core.common.config import settings
from antcode_core.common.utils.http_client import http_client
from antcode_core.infrastructure.db.tortoise import get_default_tortoise_config
from antcode_core.infrastructure.redis import RedisConnectionPool, close_redis_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期上下文管理器"""
    try:
        await init_services()
        logger.info("应用程序已启动")
        yield
    except SystemExit as e:
        logger.error(f"应用启动失败: {e}")
        logger.error("=" * 50)
        logger.error("请检查以下配置:")
        logger.error("  1. 数据库服务是否启动")
        logger.error("  2. .env 文件中的数据库连接配置是否正确")
        logger.error("  3. 网络连接是否正常")
        logger.error("=" * 50)
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "connection" in error_msg or "connect" in error_msg:
            logger.error(f"服务连接失败: {e}")
            logger.error("请检查数据库和Redis服务是否正常运行")
        else:
            logger.error(f"启动失败: {e}")
        raise
    finally:
        await shutdown_services()


async def init_services() -> None:
    """初始化所有应用服务"""
    logger.info("=" * 50)
    logger.info(f"初始化 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 50)

    logger.info("[1/13] 初始化数据库")
    await _init_db()

    logger.info("[2/13] 初始化存储")
    await _init_storage()

    logger.info("[3/13] 创建默认管理员")
    await _create_default_admin()

    logger.info("[4/13] 初始化系统配置")
    await _init_system_config()

    logger.info("[5/13] 初始化 Worker 认证")
    await _init_worker_auth()

    logger.info("[6/13] 初始化Redis")
    await _init_redis()

    logger.info("[7/12] 启动内存监控")
    await _setup_memory_monitoring()

    logger.info("[8/12] 初始化指标缓存")
    await _init_metrics_cache()

    logger.info("[9/12] 启动文件清理")
    await _init_temp_cleanup()

    logger.info("[10/12] 启动日志清理")
    await _init_log_cleanup()

    logger.info("[11/12] 启动分布式日志")
    await _init_distributed_log()

    logger.info("[12/12] 启动 HTTP 客户端")
    await http_client.start()

    logger.info("=" * 50)
    logger.info(f"{settings.APP_NAME} 初始化完成")
    logger.info(f"时区: {settings.SCHEDULER_TIMEZONE}")
    logger.info("=" * 50)


async def shutdown_services() -> None:
    """关闭所有应用服务"""
    logger.info("正在关闭服务")

    # 停止指标缓存后台任务
    try:
        from antcode_core.application.services.monitoring import system_metrics_service

        await system_metrics_service.stop_background_update()
        logger.info("指标缓存后台任务已停止")
    except Exception as e:
        logger.error(f"停止指标缓存失败: {e}")

    # 关闭 HTTP 客户端
    await http_client.stop()

    # 按逆序关闭服务
    await _shutdown_distributed_log()
    await _shutdown_log_cleanup()
    await _shutdown_temp_cleanup()
    await _shutdown_redis()

    # 关闭数据库连接
    logger.info("正在关闭数据库连接")
    await Tortoise.close_connections()

    logger.info("所有服务已关闭")
    logger.info("应用程序已停止")


# ============================================================================
# 数据库初始化
# ============================================================================


async def _init_db() -> None:
    """初始化数据库连接和模式"""
    try:
        await Tortoise.init(config=get_default_tortoise_config())
        await Tortoise.generate_schemas()
        await _ensure_task_execution_schema()
        logger.info("数据库已初始化")
    except ConnectionRefusedError:
        logger.error("无法连接数据库: 连接被拒绝，请检查数据库服务是否启动")
        db_addr = (
            settings.DATABASE_URL.split("@")[-1]
            if "@" in settings.DATABASE_URL
            else settings.DATABASE_URL
        )
        logger.error(f"数据库地址: {db_addr}")
        raise SystemExit("数据库连接失败，应用无法启动")
    except TimeoutError:
        logger.error("无法连接数据库: 连接超时，请检查网络或数据库服务状态")
        raise SystemExit("数据库连接超时，应用无法启动")
    except Exception as e:
        error_msg = str(e).lower()
        if "connection" in error_msg or "connect" in error_msg:
            logger.error(f"无法连接数据库: {e}")
            logger.error("请检查: 1) 数据库服务是否启动 2) 连接配置是否正确 3) 网络是否可达")
            raise SystemExit("数据库连接失败，应用无法启动")
        elif "access denied" in error_msg or "authentication" in error_msg:
            logger.error("数据库认证失败: 用户名或密码错误")
            raise SystemExit("数据库认证失败，应用无法启动")
        else:
            logger.error(f"数据库初始化失败: {e}")
            raise


async def _ensure_task_execution_schema() -> None:
    """确保 task_executions 表结构与最新模型一致"""
    db_url = settings.db_url.lower()
    if "mysql" in db_url or "mariadb" in db_url:
        await _ensure_task_execution_schema_mysql()
        return
    if db_url.startswith("sqlite"):
        await _ensure_task_execution_schema_sqlite()
        return
    logger.warning("当前数据库类型暂不支持自动修复 task_executions 表结构")


async def _ensure_task_execution_schema_mysql() -> None:
    db = Tortoise.get_connection("default")

    tables = await db.execute_query_dict("SHOW TABLES LIKE 'task_executions'")
    if not tables:
        return

    columns = await db.execute_query_dict("SHOW COLUMNS FROM `task_executions`")
    column_meta = {col["Field"]: col for col in columns}
    existing_cols = set(column_meta.keys())

    alters: list[str] = []
    if "dispatch_status" not in existing_cols:
        alters.append(
            "ADD COLUMN `dispatch_status` VARCHAR(11) NOT NULL DEFAULT 'pending' "
            "COMMENT 'PENDING: pending\\nDISPATCHING: dispatching\\nDISPATCHED: dispatched\\n"
            "ACKED: acked\\nREJECTED: rejected\\nTIMEOUT: timeout\\nFAILED: failed'"
        )
        existing_cols.add("dispatch_status")
    if "runtime_status" not in existing_cols:
        alters.append(
            "ADD COLUMN `runtime_status` VARCHAR(9) NULL "
            "COMMENT 'QUEUED: queued\\nRUNNING: running\\nSUCCESS: success\\nFAILED: failed\\n"
            "CANCELLED: cancelled\\nTIMEOUT: timeout\\nSKIPPED: skipped'"
        )
        existing_cols.add("runtime_status")
    if "dispatch_updated_at" not in existing_cols:
        alters.append("ADD COLUMN `dispatch_updated_at` DATETIME(6) NULL")
        existing_cols.add("dispatch_updated_at")
    if "runtime_updated_at" not in existing_cols:
        alters.append("ADD COLUMN `runtime_updated_at` DATETIME(6) NULL")
        existing_cols.add("runtime_updated_at")

    for statement in alters:
        await _safe_execute_ddl(db, f"ALTER TABLE `task_executions` {statement}")

    start_time_meta = column_meta.get("start_time")
    if start_time_meta and start_time_meta.get("Null") == "NO":
        await _safe_execute_ddl(
            db,
            "ALTER TABLE `task_executions` MODIFY COLUMN `start_time` DATETIME(6) NULL",
        )

    indexes = await db.execute_query_dict("SHOW INDEX FROM `task_executions`")
    indexed_columns = {row["Column_name"] for row in indexes}

    if "dispatch_status" in existing_cols and "dispatch_status" not in indexed_columns:
        await _safe_execute_ddl(
            db,
            "CREATE INDEX `idx_task_execut_dispatch_status` ON `task_executions` (`dispatch_status`)",
        )

    if "runtime_status" in existing_cols and "runtime_status" not in indexed_columns:
        await _safe_execute_ddl(
            db,
            "CREATE INDEX `idx_task_execut_runtime_status` ON `task_executions` (`runtime_status`)",
        )


async def _ensure_task_execution_schema_sqlite() -> None:
    db = Tortoise.get_connection("default")

    table_info = await db.execute_query_dict("PRAGMA table_info(task_executions)")
    if not table_info:
        return

    existing_cols = {row["name"] for row in table_info}

    if "dispatch_status" not in existing_cols:
        await _safe_execute_ddl(
            db,
            "ALTER TABLE task_executions ADD COLUMN dispatch_status VARCHAR(11) "
            "NOT NULL DEFAULT 'pending'",
        )
    if "runtime_status" not in existing_cols:
        await _safe_execute_ddl(
            db,
            "ALTER TABLE task_executions ADD COLUMN runtime_status VARCHAR(9) NULL",
        )
    if "dispatch_updated_at" not in existing_cols:
        await _safe_execute_ddl(
            db,
            "ALTER TABLE task_executions ADD COLUMN dispatch_updated_at DATETIME NULL",
        )
    if "runtime_updated_at" not in existing_cols:
        await _safe_execute_ddl(
            db,
            "ALTER TABLE task_executions ADD COLUMN runtime_updated_at DATETIME NULL",
        )


async def _safe_execute_ddl(db, sql: str) -> None:
    try:
        await db.execute_query(sql)
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "duplicate key name" in msg or "already exists" in msg:
            logger.warning(f"跳过已存在的结构变更: {sql}")
            return
        raise


# ============================================================================
# 存储初始化
# ============================================================================


async def _init_storage() -> None:
    """初始化存储目录"""
    try:
        storage_dirs = [
            os.path.join(settings.data_dir, "db"),
            os.path.dirname(settings.LOG_FILE_PATH),
            settings.TASK_LOG_DIR,
            settings.LOCAL_STORAGE_PATH,
            os.path.join(settings.LOCAL_STORAGE_PATH, "files"),
            os.path.join(settings.LOCAL_STORAGE_PATH, "extracted"),
            settings.TASK_EXECUTION_WORK_DIR,
        ]

        for dir_path in storage_dirs:
            os.makedirs(dir_path, exist_ok=True)

        logger.info(f"数据目录已初始化: {settings.data_dir}")
    except Exception as e:
        logger.error(f"存储初始化失败: {e}")
        raise


# ============================================================================
# 管理员用户初始化
# ============================================================================


async def _create_default_admin() -> None:
    """创建默认管理员用户（如不存在）"""
    try:
        from antcode_core.domain.schemas.user import UserCreateRequest
        from antcode_core.application.services.users.user_service import user_service

        admin_user = await user_service.get_user_by_username(settings.DEFAULT_ADMIN_USERNAME)

        if not admin_user:
            admin_request = UserCreateRequest(
                username=settings.DEFAULT_ADMIN_USERNAME,
                password=settings.DEFAULT_ADMIN_PASSWORD,
                email="admin@example.com",
                is_admin=True,
            )
            await user_service.create_user(admin_request)
            logger.info("默认管理员已创建，请使用配置的默认账号密码登录")
            logger.warning("请尽快修改默认管理员密码")
    except Exception as e:
        logger.error(f"创建默认管理员失败: {e}")
        raise


# ============================================================================
# 系统配置初始化
# ============================================================================


async def _init_system_config() -> None:
    """初始化系统配置"""
    try:
        from antcode_core.application.services.system_config import system_config_service

        await system_config_service.initialize_default_configs()
        logger.info("系统配置已初始化并加载到缓存")
    except Exception as e:
        logger.warning(f"系统配置初始化失败（非致命）: {e}")


# ============================================================================
# Worker 认证初始化
# ============================================================================


async def _init_worker_auth() -> None:
    """初始化 Worker 认证"""
    try:
        from antcode_core.application.services.workers.worker_service import worker_service

        await worker_service.init_worker_secrets()
    except Exception as e:
        logger.error(f"Worker 认证初始化失败: {e}")


# ============================================================================
# Redis 初始化
# ============================================================================


async def _init_redis() -> None:
    """初始化 Redis 连接池"""
    if not settings.REDIS_ENABLED:
        logger.info("Redis已禁用，跳过初始化")
        return

    try:
        pool_manager = await RedisConnectionPool.get_instance()
        stats = await pool_manager.get_pool_stats()
        if "error" not in stats:
            logger.info(
                f"Redis连接池: 最大={stats.get('max_connections', 'N/A')}, "
                f"available={stats.get('available_connections', 'N/A')}"
            )
        logger.info("Redis连接池已初始化")

        # 启动时清理陈旧缓存
        await _clear_stale_cache()
    except Exception as e:
        error_msg = str(e).lower()
        if "connection" in error_msg or "connect" in error_msg:
            logger.warning(f"无法连接Redis: {e}")
            logger.warning("Redis连接失败，部分功能（任务队列、缓存）将不可用")
            redis_addr = (
                settings.REDIS_URL.split("@")[-1]
                if "@" in settings.REDIS_URL
                else settings.REDIS_URL
            )
            logger.warning(f"Redis地址: {redis_addr}")
        elif "authentication" in error_msg or "auth" in error_msg:
            logger.warning("Redis认证失败: 密码错误或未配置")
            logger.warning("Redis连接失败，部分功能将不可用")
        else:
            logger.warning(f"Redis初始化失败: {e}")
            logger.warning("Redis连接失败，部分功能（任务队列、缓存）将不可用")


async def _clear_stale_cache() -> None:
    """清除启动时的旧缓存以避免数据不一致"""
    try:
        from antcode_core.infrastructure.cache import api_cache, query_cache

        # 清除项目相关缓存
        await api_cache.clear_prefix("project:")
        await query_cache.clear_prefix("project:")

        # 清除调度器相关缓存
        await api_cache.clear_prefix("scheduler:")
        await query_cache.clear_prefix("scheduler:")

        logger.info("已清除启动时的旧缓存")
    except Exception as e:
        logger.warning(f"清除旧缓存失败（非致命）: {e}")


async def _shutdown_redis() -> None:
    """关闭 Redis 连接池"""
    if not settings.REDIS_ENABLED:
        return

    try:
        await close_redis_pool()
        logger.info("Redis连接池已关闭")
    except Exception as e:
        logger.error(f"关闭Redis连接池失败: {e}")


# ============================================================================
# 内存监控
# ============================================================================


async def _setup_memory_monitoring() -> None:
    """设置内存监控"""
    from antcode_core.common.utils.memory_optimizer import setup_memory_monitoring

    await setup_memory_monitoring()


# ============================================================================
# 指标缓存初始化
# ============================================================================


async def _init_metrics_cache() -> None:
    """初始化指标缓存"""
    try:
        from antcode_core.application.services.monitoring import system_metrics_service

        if settings.METRICS_BACKGROUND_UPDATE:
            await system_metrics_service.start_background_update(settings.METRICS_UPDATE_INTERVAL)
            logger.info(
                f"指标缓存已初始化: "
                f"类型={'Redis' if settings.METRICS_USE_REDIS_CACHE else 'memory'}, "
                f"TTL={settings.METRICS_CACHE_TTL}s, "
                f"间隔={settings.METRICS_UPDATE_INTERVAL}s"
            )
        else:
            logger.info(
                f"指标缓存已初始化(按需模式): "
                f"类型={'Redis' if settings.METRICS_USE_REDIS_CACHE else 'memory'}, "
                f"TTL={settings.METRICS_CACHE_TTL}s"
            )
    except Exception as e:
        logger.error(f"指标缓存初始化失败: {e}")
        raise


# ============================================================================
# 清理服务
# ============================================================================


async def _init_temp_cleanup() -> None:
    """初始化临时文件清理服务"""
    try:
        from antcode_core.application.services.projects.temp_cleanup_service import temp_cleanup_service

        await temp_cleanup_service.start_background_cleanup(interval_hours=6)
        logger.info("文件清理服务已启动")
    except Exception as e:
        logger.warning(f"清理服务启动失败: {e}")


async def _shutdown_temp_cleanup() -> None:
    """关闭临时文件清理服务"""
    try:
        from antcode_core.application.services.projects.temp_cleanup_service import temp_cleanup_service

        await temp_cleanup_service.stop_background_cleanup()
    except Exception as e:
        logger.error(f"清理服务关闭失败: {e}")


async def _init_log_cleanup() -> None:
    """初始化日志清理服务"""
    try:
        from antcode_core.application.services.logs.log_cleanup_service import log_cleanup_service

        await log_cleanup_service.start()
        logger.info("日志清理服务已启动")
    except Exception as e:
        logger.warning(f"日志清理服务启动失败: {e}")


async def _shutdown_log_cleanup() -> None:
    """关闭日志清理服务"""
    try:
        from antcode_core.application.services.logs.log_cleanup_service import log_cleanup_service

        await log_cleanup_service.stop()
    except Exception as e:
        logger.error(f"日志清理服务关闭失败: {e}")


async def _init_distributed_log() -> None:
    """初始化分布式日志服务"""
    try:
        from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
        from antcode_web_api.websockets.log_notifier import WebSocketLogNotifier

        distributed_log_service.set_notifier(WebSocketLogNotifier())
        await distributed_log_service.start()
        logger.info("分布式日志服务已启动")
    except Exception as e:
        logger.warning(f"分布式日志服务启动失败: {e}")


async def _shutdown_distributed_log() -> None:
    """关闭分布式日志服务"""
    try:
        from antcode_core.application.services.workers.distributed_log_service import distributed_log_service

        distributed_log_service.set_notifier(None)
        await distributed_log_service.stop()
    except Exception as e:
        logger.error(f"分布式日志服务关闭失败: {e}")
