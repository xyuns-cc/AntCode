"""Application lifecycle management.

Provides the lifespan context manager and service initialization/shutdown functions.
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from loguru import logger
from tortoise import Tortoise

from src.core.config import settings
from src.infrastructure.redis import RedisConnectionPool, close_redis_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager.

    Handles startup initialization and shutdown cleanup.
    """
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
    """Initialize all application services."""
    logger.info("=" * 50)
    logger.info(f"初始化 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 50)

    logger.info("[1/12] 初始化数据库")
    await _init_db()

    logger.info("[2/12] 初始化存储")
    await _init_storage()

    logger.info("[3/12] 创建默认管理员")
    await _create_default_admin()

    logger.info("[4/12] 初始化系统配置")
    await _init_system_config()

    logger.info("[5/12] 初始化节点认证")
    await _init_node_auth()

    logger.info("[6/12] 初始化Redis")
    await _init_redis()

    logger.info("[7/12] 启动调度器")
    await _init_scheduler()

    logger.info("[8/12] 启动内存监控")
    await _setup_memory_monitoring()

    logger.info("[9/12] 初始化指标缓存")
    await _init_metrics_cache()

    logger.info("[10/12] 启动文件清理")
    await _init_temp_cleanup()

    logger.info("[11/12] 启动日志清理")
    await _init_log_cleanup()

    logger.info("[12/13] 启动分布式日志服务")
    await _init_distributed_log()

    logger.info("[13/13] 启动 gRPC 服务器")
    await _init_grpc_server()

    logger.info("=" * 50)
    logger.info(f"{settings.APP_NAME} 初始化完成")
    logger.info(f"时区: {settings.SCHEDULER_TIMEZONE}")
    if settings.GRPC_ENABLED:
        logger.info(f"gRPC 端口: {settings.GRPC_PORT}")
    logger.info("=" * 50)


async def shutdown_services() -> None:
    """Shutdown all application services."""
    logger.info("正在关闭服务")

    # Stop metrics cache background task
    try:
        from src.infrastructure.cache.metrics_cache import system_metrics_service
        await system_metrics_service.stop_background_update()
        logger.info("指标缓存后台任务已停止")
    except Exception as e:
        logger.error(f"停止指标缓存失败: {e}")

    # Shutdown gRPC server first (gracefully close all connections)
    await _shutdown_grpc_server()

    # Shutdown services in reverse order
    await _shutdown_distributed_log()
    await _shutdown_log_cleanup()
    await _shutdown_temp_cleanup()
    await _shutdown_scheduler()
    await _shutdown_redis()

    # Close database connections
    logger.info("正在关闭数据库连接")
    await Tortoise.close_connections()

    logger.info("所有服务已关闭")
    logger.info("应用程序已停止")


# ============================================================================
# Database initialization
# ============================================================================

async def _init_db() -> None:
    """Initialize database connection and schemas."""
    try:
        await Tortoise.init(config=settings.TORTOISE_ORM)
        await Tortoise.generate_schemas()
        logger.info("数据库已初始化")
    except ConnectionRefusedError:
        logger.error("无法连接数据库: 连接被拒绝，请检查数据库服务是否启动")
        db_addr = settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL
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


# ============================================================================
# Storage initialization
# ============================================================================

async def _init_storage() -> None:
    """Initialize storage directories."""
    try:
        storage_dirs = [
            os.path.join(settings.data_dir, "db"),
            os.path.dirname(settings.LOG_FILE_PATH),
            settings.TASK_LOG_DIR,
            settings.LOCAL_STORAGE_PATH,
            os.path.join(settings.LOCAL_STORAGE_PATH, "files"),
            os.path.join(settings.LOCAL_STORAGE_PATH, "extracted"),
            settings.TASK_EXECUTION_WORK_DIR,
            settings.VENV_STORAGE_ROOT,
            os.path.join(settings.VENV_STORAGE_ROOT, "shared"),
            settings.MISE_DATA_ROOT,
            os.path.join(settings.MISE_DATA_ROOT, "cache"),
        ]

        for dir_path in storage_dirs:
            os.makedirs(dir_path, exist_ok=True)

        logger.info(f"数据目录已初始化: {settings.data_dir}")
    except Exception as e:
        logger.error(f"存储初始化失败: {e}")
        raise


# ============================================================================
# Admin user initialization
# ============================================================================

async def _create_default_admin() -> None:
    """Create default admin user if not exists."""
    try:
        from src.services.users.user_service import user_service
        from src.schemas.user import UserCreateRequest

        admin_user = await user_service.get_user_by_username(settings.DEFAULT_ADMIN_USERNAME)

        if not admin_user:
            admin_request = UserCreateRequest(
                username=settings.DEFAULT_ADMIN_USERNAME,
                password=settings.DEFAULT_ADMIN_PASSWORD,
                email="admin@example.com",
                is_admin=True,
            )
            await user_service.create_user(admin_request)
            logger.info(f"默认管理员已创建: {settings.DEFAULT_ADMIN_USERNAME}")
            logger.warning("请尽快修改默认管理员密码（DEFAULT_ADMIN_PASSWORD）")
    except Exception as e:
        logger.error(f"创建默认管理员失败: {e}")
        raise


# ============================================================================
# System config initialization
# ============================================================================

async def _init_system_config() -> None:
    """Initialize system configuration."""
    try:
        from src.services.system_config import system_config_service
        await system_config_service.initialize_default_configs()
        logger.info("系统配置已初始化并加载到缓存")
    except Exception as e:
        logger.warning(f"系统配置初始化失败（非致命）: {e}")


# ============================================================================
# Node authentication initialization
# ============================================================================

async def _init_node_auth() -> None:
    """Initialize node authentication."""
    try:
        from src.services.nodes.node_service import node_service
        await node_service.init_node_secrets()
    except Exception as e:
        logger.error(f"节点认证初始化失败: {e}")


# ============================================================================
# Redis initialization
# ============================================================================

async def _init_redis() -> None:
    """Initialize Redis connection pool."""
    if not settings.REDIS_ENABLED:
        logger.info("Redis已禁用，跳过初始化")
        return

    try:
        pool_manager = await RedisConnectionPool.get_instance()
        stats = await pool_manager.get_pool_stats()
        if "error" not in stats:
            logger.info(f"Redis连接池: 最大={stats.get('max_connections', 'N/A')}, "
                       f"available={stats.get('available_connections', 'N/A')}")
        logger.info("Redis连接池已初始化")

        # Clear stale cache on startup
        await _clear_stale_cache()
    except Exception as e:
        error_msg = str(e).lower()
        if "connection" in error_msg or "connect" in error_msg:
            logger.warning(f"无法连接Redis: {e}")
            logger.warning("Redis连接失败，部分功能（任务队列、缓存）将不可用")
            redis_addr = settings.REDIS_URL.split('@')[-1] if '@' in settings.REDIS_URL else settings.REDIS_URL
            logger.warning(f"Redis地址: {redis_addr}")
        elif "authentication" in error_msg or "auth" in error_msg:
            logger.warning("Redis认证失败: 密码错误或未配置")
            logger.warning("Redis连接失败，部分功能将不可用")
        else:
            logger.warning(f"Redis初始化失败: {e}")
            logger.warning("Redis连接失败，部分功能（任务队列、缓存）将不可用")


async def _clear_stale_cache() -> None:
    """Clear stale cache on startup to avoid data inconsistency."""
    try:
        from src.infrastructure.cache import api_cache, query_cache

        # Clear project-related cache
        await api_cache.clear_prefix("project:")
        await query_cache.clear_prefix("project:")

        # Clear scheduler-related cache
        await api_cache.clear_prefix("scheduler:")
        await query_cache.clear_prefix("scheduler:")

        logger.info("已清除启动时的旧缓存")
    except Exception as e:
        logger.warning(f"清除旧缓存失败（非致命）: {e}")


async def _shutdown_redis() -> None:
    """Shutdown Redis connection pool."""
    if not settings.REDIS_ENABLED:
        return

    try:
        await close_redis_pool()
        logger.info("Redis连接池已关闭")
    except Exception as e:
        logger.error(f"关闭Redis连接池失败: {e}")


# ============================================================================
# Scheduler initialization
# ============================================================================

async def _init_scheduler() -> None:
    """Initialize task scheduler."""
    try:
        from src.services.scheduler.scheduler_service import scheduler_service
        await scheduler_service.start()
        logger.info("任务调度器已启动")

        if settings.REDIS_ENABLED:
            logger.info("调度器已配置Redis任务队列")

        # Recover interrupted tasks
        await _recover_interrupted_tasks()
    except Exception as e:
        logger.error(f"调度器启动失败: {e}")
        logger.warning("应用程序在无调度器模式下运行")


async def _recover_interrupted_tasks() -> None:
    """Recover interrupted tasks on startup."""
    try:
        from src.services.scheduler.task_persistence import task_recovery_service
        stats = await task_recovery_service.recover_on_startup()
        if stats['recovered'] > 0:
            logger.info(f"已恢复 {stats['recovered']} 个中断的任务")
    except Exception as e:
        logger.warning(f"任务恢复失败（非致命）: {e}")


async def _shutdown_scheduler() -> None:
    """Shutdown task scheduler."""
    try:
        from src.services.scheduler.scheduler_service import scheduler_service
        await scheduler_service.shutdown()
        logger.info("任务调度器已停止")
    except Exception as e:
        logger.error(f"调度器关闭失败: {e}")


# ============================================================================
# Memory monitoring
# ============================================================================

async def _setup_memory_monitoring() -> None:
    """Setup memory monitoring."""
    from src.utils.memory_optimizer import setup_memory_monitoring
    await setup_memory_monitoring()


# ============================================================================
# Metrics cache initialization
# ============================================================================

async def _init_metrics_cache() -> None:
    """Initialize metrics cache."""
    try:
        from src.infrastructure.cache.metrics_cache import system_metrics_service

        if settings.METRICS_BACKGROUND_UPDATE:
            await system_metrics_service.start_background_update(settings.METRICS_UPDATE_INTERVAL)
            logger.info(f"指标缓存已初始化: "
                       f"类型={'Redis' if settings.METRICS_USE_REDIS_CACHE else 'memory'}, "
                       f"TTL={settings.METRICS_CACHE_TTL}s, "
                       f"间隔={settings.METRICS_UPDATE_INTERVAL}s")
        else:
            logger.info(f"指标缓存已初始化(按需模式): "
                       f"类型={'Redis' if settings.METRICS_USE_REDIS_CACHE else 'memory'}, "
                       f"TTL={settings.METRICS_CACHE_TTL}s")
    except Exception as e:
        logger.error(f"指标缓存初始化失败: {e}")
        raise


# ============================================================================
# Cleanup services
# ============================================================================

async def _init_temp_cleanup() -> None:
    """Initialize temporary file cleanup service."""
    try:
        from src.services.projects.temp_cleanup_service import temp_cleanup_service
        await temp_cleanup_service.start_background_cleanup(interval_hours=6)
        logger.info("文件清理服务已启动")
    except Exception as e:
        logger.warning(f"清理服务启动失败: {e}")


async def _shutdown_temp_cleanup() -> None:
    """Shutdown temporary file cleanup service."""
    try:
        from src.services.projects.temp_cleanup_service import temp_cleanup_service
        await temp_cleanup_service.stop_background_cleanup()
    except Exception as e:
        logger.error(f"清理服务关闭失败: {e}")


async def _init_log_cleanup() -> None:
    """Initialize log cleanup service."""
    try:
        from src.services.logs.log_cleanup_service import log_cleanup_service
        await log_cleanup_service.start()
        logger.info("日志清理服务已启动")
    except Exception as e:
        logger.warning(f"日志清理服务启动失败: {e}")


async def _shutdown_log_cleanup() -> None:
    """Shutdown log cleanup service."""
    try:
        from src.services.logs.log_cleanup_service import log_cleanup_service
        await log_cleanup_service.stop()
    except Exception as e:
        logger.error(f"日志清理服务关闭失败: {e}")


async def _init_distributed_log() -> None:
    """Initialize distributed log service."""
    try:
        from src.services.nodes.distributed_log_service import distributed_log_service
        await distributed_log_service.start()
        logger.info("分布式日志服务已启动")
    except Exception as e:
        logger.warning(f"分布式日志服务启动失败: {e}")


async def _shutdown_distributed_log() -> None:
    """Shutdown distributed log service."""
    try:
        from src.services.nodes.distributed_log_service import distributed_log_service
        await distributed_log_service.stop()
    except Exception as e:
        logger.error(f"分布式日志服务关闭失败: {e}")


# ============================================================================
# gRPC server initialization
# ============================================================================

async def _init_grpc_server() -> None:
    """Initialize gRPC server."""
    if not settings.GRPC_ENABLED:
        logger.info("gRPC 服务已禁用，跳过初始化")
        return

    try:
        from src.services.grpc.server import get_grpc_server
        from src.services.grpc.node_service_impl import get_node_service_impl
        from src.services.grpc.dispatcher import message_dispatcher
        from src.services.grpc.handlers import (
            HeartbeatHandler,
            LogHandler,
            TaskStatusHandler,
        )

        # 获取 gRPC 服务器实例
        grpc_server = get_grpc_server()

        # 获取 NodeService 实现
        node_service_impl = get_node_service_impl()

        # 注册消息处理器
        message_dispatcher.register("heartbeat", HeartbeatHandler())
        message_dispatcher.register("log_batch", LogHandler())
        message_dispatcher.register("task_status", TaskStatusHandler())
        logger.debug("已注册 gRPC 消息处理器")

        # 设置服务实现
        grpc_server.set_servicer(node_service_impl)

        # 启动服务器
        started = await grpc_server.start()
        if started:
            logger.info(
                f"gRPC 服务器已启动 - 端口: {settings.GRPC_PORT}, "
                f"最大工作线程: {settings.GRPC_MAX_WORKERS}"
            )
        else:
            logger.warning("gRPC 服务器启动失败")

    except ImportError as e:
        logger.warning(f"gRPC 依赖未安装，跳过初始化: {e}")
    except Exception as e:
        logger.error(f"gRPC 服务器初始化失败: {e}")
        logger.warning("应用程序在无 gRPC 模式下运行")


async def _shutdown_grpc_server() -> None:
    """Shutdown gRPC server."""
    if not settings.GRPC_ENABLED:
        return

    try:
        from src.services.grpc.server import get_grpc_server

        grpc_server = get_grpc_server()
        if grpc_server.is_running:
            await grpc_server.stop(grace_period=settings.GRPC_SHUTDOWN_GRACE_PERIOD)
            logger.info("gRPC 服务器已停止")
    except ImportError:
        pass  # gRPC 未安装
    except Exception as e:
        logger.error(f"gRPC 服务器关闭失败: {e}")
