"""应用初始化"""
import os

from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from tortoise import Tortoise

from src.core.config import settings
from src.core.middleware import (
    AdminPermissionMiddleware, SecurityHeadersMiddleware,
    RateLimitMiddleware, CacheInvalidationMiddleware
)
from src.schemas.user import UserCreateRequest
from src.services.scheduler.scheduler_service import scheduler_service
from src.services.users.user_service import user_service
from src.utils.memory_optimizer import setup_memory_monitoring
from src.utils.metrics_cache import system_metrics_service
from src.utils.redis_pool import RedisConnectionPool, close_redis_pool


def make_middlewares():
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=settings.CORS_ALLOW_METHODS,
            allow_headers=settings.CORS_ALLOW_HEADERS,
        ),
        Middleware(SecurityHeadersMiddleware),
        Middleware(RateLimitMiddleware, calls=100, period=60),
        Middleware(AdminPermissionMiddleware),
        Middleware(CacheInvalidationMiddleware),
    ]
    return middleware


async def init_db():
    try:
        await Tortoise.init(config=settings.TORTOISE_ORM)
        await Tortoise.generate_schemas()
        logger.info("数据库已初始化")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise


async def create_default_admin():
    try:
        admin_user = await user_service.get_user_by_username(settings.DEFAULT_ADMIN_USERNAME)
        
        if not admin_user:
            admin_request = UserCreateRequest(
                username=settings.DEFAULT_ADMIN_USERNAME,
                password=settings.DEFAULT_ADMIN_PASSWORD,
                email="admin@example.com",
                is_admin=True,
            )
            await user_service.create_user(admin_request)
            logger.info("默认管理员已创建: admin/admin")
            logger.warning("请修改默认管理员密码")
    except Exception as e:
        logger.error(f"创建默认管理员失败: {e}")
        raise


async def init_storage():
    try:
        # 数据目录结构：
        # data/
        # ├── db/           # 数据库文件
        # ├── logs/         # 日志文件
        # │   └── tasks/    # 任务执行日志
        # └── storage/      # 存储文件
        #     ├── projects/ # 项目文件
        #     ├── executions/ # 执行临时目录
        #     ├── venvs/    # 虚拟环境
        #     └── mise/     # mise 数据
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


async def init_redis():
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
    except Exception as e:
        logger.error(f"Redis初始化失败: {e}")
        if settings.REDIS_ENABLED:
            logger.warning("Redis已启用但连接失败，任务队列不可用")


async def shutdown_redis():
    if not settings.REDIS_ENABLED:
        return

    try:
        await close_redis_pool()
        logger.info("Redis连接池已关闭")
    except Exception as e:
        logger.error(f"关闭Redis连接池失败: {e}")


async def init_scheduler():
    try:
        await scheduler_service.start()
        logger.info("任务调度器已启动")

        if settings.REDIS_ENABLED:
            logger.info("调度器已配置Redis任务队列")
    except Exception as e:
        logger.error(f"调度器启动失败: {e}")
        logger.warning("应用程序在无调度器模式下运行")


async def shutdown_scheduler():
    try:
        await scheduler_service.shutdown()
        logger.info("任务调度器已停止")
    except Exception as e:
        logger.error(f"调度器关闭失败: {e}")


async def init_data():
    logger.info("=" * 50)
    logger.info(f"初始化 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 50)

    logger.info("[1/7] 初始化数据库")
    await init_db()

    logger.info("[2/7] 初始化存储")
    await init_storage()

    logger.info("[3/7] 创建默认管理员")
    await create_default_admin()

    logger.info("[4/7] 初始化Redis")
    await init_redis()

    logger.info("[5/7] 启动调度器")
    await init_scheduler()

    logger.info("[6/7] 启动内存监控")
    await setup_memory_monitoring()

    logger.info("[7/7] 初始化指标缓存")
    await init_metrics_cache()

    logger.info("=" * 50)
    logger.info(f"{settings.APP_NAME} 初始化完成")
    logger.info(f"工作节点ID: {settings.WORKER_ID}")
    logger.info(f"时区: {settings.SCHEDULER_TIMEZONE}")
    logger.info("=" * 50)


async def init_metrics_cache():
    try:
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


async def shutdown_services():
    logger.info("正在关闭服务")

    try:
        await system_metrics_service.stop_background_update()
        logger.info("指标缓存后台任务已停止")
    except Exception as e:
        logger.error(f"停止指标缓存失败: {e}")

    await shutdown_scheduler()
    await shutdown_redis()
    await Tortoise.close_connections()

    logger.info("所有服务已关闭")
