# src/core/init_app.py
import os
import shutil

from aerich import Command
from tortoise.exceptions import OperationalError
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from tortoise import Tortoise

from src.core.config import settings
from src.core.middleware import (
    AdminPermissionMiddleware,
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
    CacheInvalidationMiddleware,
)
from src.utils.memory_optimizer import setup_memory_monitoring
from src.utils.redis_pool import RedisConnectionPool, close_redis_pool


def make_middlewares():
    """创建中间件"""
    middleware = [
        # CORS中间件
        Middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=settings.CORS_ALLOW_METHODS,
            allow_headers=settings.CORS_ALLOW_HEADERS,
        ),
        # 安全头部中间件
        Middleware(SecurityHeadersMiddleware),
        # 速率限制中间件（100请求/分钟）
        Middleware(RateLimitMiddleware, calls=100, period=60),
        # 管理员权限验证中间件
        Middleware(AdminPermissionMiddleware),
        # 写操作后强制清理API缓存
        Middleware(CacheInvalidationMiddleware),
    ]
    return middleware


async def init_db():
    """初始化数据库"""
    command = Command(tortoise_config=settings.TORTOISE_ORM)
    try:
        await command.init_db(safe=True)
    except FileExistsError:
        pass

    await command.init()
    try:
        await command.migrate()
    except AttributeError:
        logger.warning("unable to retrieve model history from database, model history will be created from scratch")
        shutil.rmtree("migrations")
        await command.init_db(safe=True)

    try:
        await command.upgrade(run_in_transaction=True)
    except OperationalError as e:
        # 处理SQLite对约束变更的限制：回退并重新初始化迁移
        logger.warning(f"数据库升级失败，尝试重建迁移: {e}")
        shutil.rmtree("migrations", ignore_errors=True)
        await command.init_db(safe=True)
        await command.init()
        await command.migrate()
        await command.upgrade(run_in_transaction=True)


async def create_default_admin():
    """创建默认管理员用户"""
    try:
        from src.services.users.user_service import user_service
        from src.schemas.user import UserCreateRequest
        
        # 检查管理员是否已存在
        admin_user = await user_service.get_user_by_username(settings.DEFAULT_ADMIN_USERNAME)
        
        if not admin_user:
            # 创建管理员用户（密码来源于配置，符合长度要求）
            admin_request = UserCreateRequest(
                username=settings.DEFAULT_ADMIN_USERNAME,
                password=settings.DEFAULT_ADMIN_PASSWORD,
                email="admin@example.com",
                is_admin=True,
            )
            await user_service.create_user(admin_request)
            logger.info("✅ 默认管理员已创建: 用户名=admin / 密码=admin")
            logger.warning("⚠️ 出于安全考虑，请尽快登录后修改默认管理员密码")
    except Exception as e:
        logger.error(f"创建默认管理员用户失败: {e}")
        raise


async def init_storage():
    """初始化存储目录"""
    try:
        # 创建实际需要的存储目录
        storage_dirs = [
            settings.LOCAL_STORAGE_PATH,  # 项目存储根目录
            f"{settings.LOCAL_STORAGE_PATH}/files",  # 原始文件存储目录
            f"{settings.LOCAL_STORAGE_PATH}/extracted",  # 压缩文件解压目录
            f"{settings.LOCAL_STORAGE_PATH}/executions",  # 任务执行工作目录
            settings.TASK_LOG_DIR,  # 任务日志目录（logs/tasks）
            settings.VENV_STORAGE_ROOT,  # 虚拟环境根目录（venvs）
            f"{settings.VENV_STORAGE_ROOT}/shared",  # 共享虚拟环境目录
            settings.MISE_DATA_ROOT,  # mise 数据根目录
            f"{settings.MISE_DATA_ROOT}/cache",  # mise 缓存目录
        ]

        for dir_path in storage_dirs:
            os.makedirs(dir_path, exist_ok=True)

        logger.info("✅ 存储目录初始化完成")
        logger.info(f"   📁 项目存储目录: {settings.LOCAL_STORAGE_PATH}")
        logger.info(f"   📁 原始文件目录: {settings.LOCAL_STORAGE_PATH}/files")
        logger.info(f"   📁 文件解压目录: {settings.LOCAL_STORAGE_PATH}/extracted")
        logger.info(f"   📁 任务执行目录: {settings.LOCAL_STORAGE_PATH}/executions")
        logger.info(f"   📁 任务日志目录: {settings.TASK_LOG_DIR}")
        logger.info(f"   📁 虚拟环境根目录: {settings.VENV_STORAGE_ROOT}")
        logger.info(f"   📁 共享虚拟环境目录: {settings.VENV_STORAGE_ROOT}/shared")
        logger.info(f"   📁 mise 数据根目录: {settings.MISE_DATA_ROOT}")
        logger.info(f"   📁 mise 缓存目录: {settings.MISE_DATA_ROOT}/cache")

    except Exception as e:
        logger.error(f"存储目录初始化失败: {e}")
        raise


async def init_redis():
    """初始化Redis连接池"""
    if not settings.REDIS_ENABLED:
        logger.info("⚠️ Redis未启用，跳过Redis初始化")
        return

    try:
        # 初始化Redis连接池
        pool_manager = await RedisConnectionPool.get_instance()
        
        # 获取连接池统计信息
        stats = await pool_manager.get_pool_stats()
        if "error" not in stats:
            logger.info(f"📋 Redis连接池统计:")
            logger.info(f"   最大连接数: {stats.get('max_connections', 'N/A')}")
            logger.info(f"   可用连接数: {stats.get('available_connections', 'N/A')}")
            logger.info(f"   使用中连接数: {stats.get('in_use_connections', 'N/A')}")

        logger.info("✅ Redis连接池初始化完成")

    except Exception as e:
        logger.error(f"❌ Redis连接池初始化失败: {e}")
        if settings.REDIS_ENABLED:
            logger.warning("⚠️ Redis配置已启用但连接失败，规则任务功能将不可用")
            logger.warning("   请检查Redis服务是否运行，以及密码是否正确")
        # Redis初始化失败不阻止应用启动，但记录警告


async def shutdown_redis():
    """关闭Redis连接池"""
    if not settings.REDIS_ENABLED:
        return

    try:
        await close_redis_pool()
        logger.info("✅ Redis连接池已关闭")
    except Exception as e:
        logger.error(f"关闭Redis连接池失败: {e}")


async def init_scheduler():
    """初始化调度器"""
    try:
        from src.services.scheduler.scheduler_service import scheduler_service
        await scheduler_service.start()
        logger.info("✅ 任务调度器启动成功")

        # 如果Redis已启用，调度器将使用Redis任务服务
        if settings.REDIS_ENABLED:
            logger.info("   调度器已配置为使用Redis任务队列")

    except Exception as e:
        logger.error(f"任务调度器启动失败: {e}")
        # 调度器启动失败不影响主应用运行
        logger.warning("⚠️ 应用将在无调度器模式下运行")


async def shutdown_scheduler():
    """关闭调度器"""
    try:
        from src.services.scheduler.scheduler_service import scheduler_service
        await scheduler_service.shutdown()
        logger.info("✅ 任务调度器已关闭")
    except Exception as e:
        logger.error(f"任务调度器关闭失败: {e}")





async def init_data():
    """初始化应用数据和服务"""
    logger.info("=" * 50)
    logger.info(f"🚀 正在初始化 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 50)

    # 1. 初始化数据库
    logger.info("📦 步骤 1/6: 初始化数据库...")
    await init_db()

    # 2. 初始化存储目录
    logger.info("📁 步骤 2/6: 初始化存储目录...")
    await init_storage()

    # 3. 创建默认管理员用户
    logger.info("👤 步骤 3/6: 创建默认管理员...")
    await create_default_admin()

    # 4. 初始化Redis（如果启用）
    logger.info("🔌 步骤 4/7: 初始化Redis服务...")
    await init_redis()

    # 5. 初始化调度器
    logger.info("⏰ 步骤 5/7: 初始化任务调度器...")
    await init_scheduler()

    # 6. 启动内存监控
    logger.info("🧠 步骤 6/7: 启动内存监控...")
    await setup_memory_monitoring()

    # 7. 初始化系统指标缓存
    logger.info("📊 步骤 7/7: 初始化系统指标缓存...")
    await init_metrics_cache()

    logger.info("=" * 50)
    logger.info(f"✅ {settings.APP_NAME} 初始化完成！")
    logger.info(f"📡 Worker ID: {settings.WORKER_ID}")
    logger.info(f"🌍 时区: {settings.SCHEDULER_TIMEZONE}")
    logger.info("=" * 50)


async def init_metrics_cache():
    """初始化系统指标缓存"""
    try:
        from src.utils.metrics_cache import system_metrics_service
        
        # 如果启用了后台更新，启动后台任务
        if settings.METRICS_BACKGROUND_UPDATE:
            await system_metrics_service.start_background_update(settings.METRICS_UPDATE_INTERVAL)
            logger.info("✅ 系统指标缓存初始化完成")
            logger.info(f"   缓存类型: {'Redis' if settings.METRICS_USE_REDIS_CACHE else '内存'}")
            logger.info(f"   缓存TTL: {settings.METRICS_CACHE_TTL}秒")
            logger.info(f"   后台更新: 已启用（间隔: {settings.METRICS_UPDATE_INTERVAL}秒）")
        else:
            logger.info("✅ 系统指标缓存初始化完成（仅按需缓存）")
            logger.info(f"   缓存类型: {'Redis' if settings.METRICS_USE_REDIS_CACHE else '内存'}")
            logger.info(f"   缓存TTL: {settings.METRICS_CACHE_TTL}秒")
            
    except Exception as e:
        logger.error(f"系统指标缓存初始化失败: {e}")
        raise


async def shutdown_services():
    """关闭所有服务（应用关闭时调用）"""
    logger.info("正在关闭应用服务...")

    # 停止系统指标缓存后台任务
    try:
        from src.utils.metrics_cache import system_metrics_service
        await system_metrics_service.stop_background_update()
        logger.info("✅ 系统指标缓存后台任务已停止")
    except Exception as e:
        logger.error(f"停止系统指标缓存失败: {e}")

    # WebSocket服务已移除，跳过关闭

    # 关闭调度器
    await shutdown_scheduler()

    # 关闭Redis连接
    await shutdown_redis()

    # 关闭数据库连接
    await Tortoise.close_connections()

    logger.info("✅ 所有服务已安全关闭")
