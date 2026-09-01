"""应用生命周期管理

提供生命周期上下文管理器和服务初始化/关闭函数。
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager

from antcode_core.common.config import settings
from antcode_core.common.utils.http_client import http_client
from antcode_core.infrastructure.db.tortoise import get_default_tortoise_config
from antcode_core.infrastructure.redis import RedisConnectionPool, close_redis_pool
from fastapi import FastAPI
from loguru import logger
from tortoise import Tortoise

from antcode_web_api.services.worker_installer import validate_required_worker_install_config

# 关闭单步超时（秒）：任何 shutdown 子任务卡住不应阻塞进程退出
_SHUTDOWN_STEP_TIMEOUT = 10.0


async def _run_with_timeout(name: str, coro: Awaitable) -> None:
    """把 shutdown 单步包成带超时的执行，避免整体优雅关闭被单个卡住的资源拖垮。"""
    try:
        await asyncio.wait_for(coro, timeout=_SHUTDOWN_STEP_TIMEOUT)
    except TimeoutError:
        logger.warning(f"关闭步骤 {name} 超过 {_SHUTDOWN_STEP_TIMEOUT}s 未完成，强制跳过")
    except Exception as e:
        logger.error(f"关闭步骤 {name} 失败: {e}")


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
    """初始化所有应用服务。

    这里**不**引导默认管理员：唯一的创建方是 ``scripts/init_db.py::_create_admin``。
    两个理由，缺一都会退回到原来的坏形态：

    1. 本函数按 ``SERVER_WORKERS``（默认 2）在每个 uvicorn 子进程各跑一遍。曾经的
       ``_create_default_admin`` 是 ``get_or_none`` 后 ``create_user`` 的 check-then-act，
       空库时两个进程同时走到就必然有一个吃 ``users_username_key`` 唯一冲突，异常
       冒泡出 lifespan → uvicorn 判 "Application startup failed" 杀掉该子进程 →
       父进程重拉，服务在半容量下待若干秒。init_db 是一次性单进程，没有这个竞态。
    2. 生产刻意不把 ``DEFAULT_ADMIN_PASSWORD`` 给常驻的 web-api，只在
       ``bootstrap-admin.sh`` 那次一次性 migration 里以 secret 文件形态注入
       （见 docker-compose.prod.bootstrap-admin.yml 与 test_docker_compose_prod_contract）。
       在常驻服务里读这个变量建号会把一次性引导密钥变成长期环境变量。

    web-api 本身永远不建表（``_init_db`` 只 ``Tortoise.init``，不 ``generate_schemas``），
    所以任何部署形态都必须先跑 init_db，管理员也就一定被创建过。
    """
    logger.info("=" * 50)
    logger.info(f"初始化 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 50)

    # 生产画像必须在数据库连接前暴露安装分发配置错误。
    logger.info("[1/11] 校验 Worker 安装分发配置")
    validate_required_worker_install_config(settings)

    logger.info("[2/11] 初始化数据库")
    await _init_db()

    logger.info("[3/11] 初始化运行目录")
    await _init_runtime_dirs()

    logger.info("[4/11] 初始化系统配置")
    await _init_system_config()
    await _init_alert_service()

    logger.info("[5/11] 初始化 Worker 认证")
    await _init_worker_auth()

    logger.info("[6/11] 初始化Redis")
    await _init_redis()

    logger.info("[7/11] 启动内存监控")
    await _setup_memory_monitoring()

    logger.info("[8/11] 初始化指标缓存")
    await _init_metrics_cache()

    logger.info("[9/11] 启动日志清理")
    await _init_log_cleanup()

    logger.info("[10/11] 启动分布式日志")
    await _init_distributed_log()

    logger.info("[11/11] 启动 HTTP 客户端")
    await http_client.start()

    logger.info("=" * 50)
    logger.info(f"{settings.APP_NAME} 初始化完成")
    logger.info(f"时区: {settings.SCHEDULER_TIMEZONE}")
    logger.info("=" * 50)


async def shutdown_services() -> None:
    """关闭所有应用服务。每一步都套 asyncio.wait_for，防止单个资源卡住阻塞进程退出。"""
    logger.info("正在关闭服务")

    from antcode_core.application.services.monitoring import system_metrics_service

    await _run_with_timeout("stop_metrics_background", system_metrics_service.stop_background_update())
    await _run_with_timeout("http_client.stop", http_client.stop())
    # P2-a1: drain 后台任务 (batch_delete / submit_background_task),避免 uvicorn 强杀
    # 造成半写数据 / 未 flush 日志。放在 distributed_log / redis 关闭前,让后台任务先收尾。
    try:
        from antcode_core.common.utils.api_optimizer import async_processor

        await _run_with_timeout("async_processor.shutdown", async_processor.shutdown(timeout=8.0))
    except Exception as exc:
        logger.warning(f"async_processor.shutdown 失败(非致命): {exc}")
    await _run_with_timeout("shutdown_distributed_log", _shutdown_distributed_log())
    await _run_with_timeout("shutdown_log_cleanup", _shutdown_log_cleanup())
    await _run_with_timeout("shutdown_redis", _shutdown_redis())

    logger.info("正在关闭数据库连接")
    await _run_with_timeout("close_db_connections", Tortoise.close_connections())

    logger.info("所有服务已关闭")
    logger.info("应用程序已停止")


# 数据库初始化


async def _init_db() -> None:
    """初始化数据库连接；建表只由 scripts/init_db.py 负责。"""
    try:
        await Tortoise.init(config=get_default_tortoise_config(service="web_api"))
        logger.info("数据库连接已初始化")
    except ConnectionRefusedError:
        logger.error("无法连接数据库: 连接被拒绝，请检查数据库服务是否启动")
        db_addr = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL
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


# 运行目录初始化


async def _init_runtime_dirs() -> None:
    """初始化本地运行目录。持久化数据与日志均写入 PostgreSQL。"""
    try:
        runtime_dirs = [
            settings.TASK_EXECUTION_WORK_DIR,
        ]

        for dir_path in runtime_dirs:
            os.makedirs(dir_path, exist_ok=True)

        logger.info(f"运行目录已初始化: {settings.data_dir}")
    except Exception as e:
        logger.error(f"运行目录初始化失败: {e}")
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
        logger.error(f"系统配置初始化失败: {e}")
        raise


async def _init_alert_service() -> None:
    """L4: 显式初始化 alert_service，避免懒加载"""
    try:
        from antcode_core.application.services.alert.alert_service import alert_service

        await alert_service.initialize()
        logger.info("alert_service 已初始化")
    except Exception as e:
        logger.warning(f"alert_service 初始化失败（非致命）: {e}")


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
        raise


# ============================================================================
# Redis 初始化
# ============================================================================


async def _init_redis() -> None:
    """初始化 Redis 连接池"""
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
        logger.error(f"Redis 初始化失败: {e}")
        raise


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
        logger.error(f"清除旧缓存失败: {e}")
        raise


async def _shutdown_redis() -> None:
    """关闭 Redis 连接池"""
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
                f"类型=Redis, "
                f"TTL={settings.METRICS_CACHE_TTL}s, "
                f"间隔={settings.METRICS_UPDATE_INTERVAL}s"
            )
        else:
            logger.info(f"指标缓存已初始化(按需模式): 类型=Redis, TTL={settings.METRICS_CACHE_TTL}s")
    except Exception as e:
        logger.error(f"指标缓存初始化失败: {e}")
        raise


# ============================================================================
# 清理服务
# ============================================================================


async def _init_log_cleanup() -> None:
    """初始化日志清理服务"""
    try:
        from antcode_core.application.services.logs.log_cleanup_service import log_cleanup_service

        await log_cleanup_service.start()
        logger.info("日志清理服务已启动")
    except Exception as e:
        logger.error(f"日志清理服务启动失败: {e}")
        raise


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

        from antcode_web_api.streams.ingest_follower import ingest_log_follower
        from antcode_web_api.streams.log_notifier import SSELogNotifier

        await ingest_log_follower.start()
        distributed_log_service.set_notifier(SSELogNotifier())
        await distributed_log_service.start()
        logger.info("分布式日志服务已启动")
    except Exception as e:
        logger.error(f"分布式日志服务启动失败: {e}")
        raise


async def _shutdown_distributed_log() -> None:
    """关闭分布式日志服务"""
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service

    from antcode_web_api.streams.ingest_follower import ingest_log_follower
    from antcode_web_api.streams.run_stream_broker import run_stream_broker

    errors: list[Exception] = []
    try:
        # P2 §4.2: 先释放 SSE 全局容量租约，避免重启窗口内幽灵租约占位。
        await run_stream_broker.shutdown()
    except Exception as exc:
        errors.append(exc)
        logger.exception("SSE broker 关闭失败")
    try:
        await ingest_log_follower.shutdown()
    except Exception as exc:
        errors.append(exc)
        logger.exception("ingest follower 关闭失败")
    try:
        distributed_log_service.set_notifier(None)
    except Exception as exc:
        errors.append(exc)
        logger.exception("分布式日志 notifier 解绑失败")
    try:
        await distributed_log_service.stop()
    except Exception as exc:
        errors.append(exc)
        logger.exception("分布式日志 push service 关闭失败")
    if errors:
        raise ExceptionGroup("分布式日志服务关闭存在失败步骤", errors)
