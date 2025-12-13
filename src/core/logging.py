"""日志配置模块"""
import os
import sys
import asyncio

from loguru import logger

from src.core.config import settings

# 日志格式
CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"


def setup_logging():
    """初始化日志系统"""
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=settings.LOG_LEVEL,
        colorize=True,
    )

    # 文件输出
    if settings.LOG_TO_FILE:
        log_dir = os.path.dirname(settings.LOG_FILE_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        logger.add(
            settings.LOG_FILE_PATH,
            format=FILE_FORMAT,
            level=settings.LOG_LEVEL,
            rotation="500 MB",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
            enqueue=True,  # 异步写入，提升性能
        )

    # 添加告警处理器（仅处理 ERROR 和 CRITICAL）
    logger.add(
        _alert_sink,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
        level="ERROR",
        filter=lambda record: record["level"].name in ["ERROR", "CRITICAL"],
        enqueue=True,  # 异步处理，避免阻塞主线程
    )

    logger.info(f"日志初始化完成: level={settings.LOG_LEVEL}, file={settings.LOG_TO_FILE}, alert=True")


def _alert_sink(message):
    """告警处理器 - 将 ERROR/CRITICAL 日志发送到告警渠道"""
    try:
        record = message.record
        level = record["level"].name

        # 构建告警内容
        title = f"系统{level}告警"
        content = f"""
**时间**: {record['time'].strftime('%Y-%m-%d %H:%M:%S')}
**级别**: {level}
**位置**: {record['name']}:{record['function']}:{record['line']}
**消息**: {record['message']}
"""

        # 如果有异常信息，添加堆栈
        if record.get('exception'):
            exc_info = record['exception']
            if exc_info:
                content += f"\n**异常类型**: {exc_info.type.__name__ if exc_info.type else 'Unknown'}"
                if exc_info.value:
                    content += f"\n**异常信息**: {str(exc_info.value)[:500]}"

        # 异步发送告警
        _send_alert_async(level.lower(), title, content)

    except Exception as e:
        # 避免告警系统本身的错误影响主系统
        print(f"告警发送失败: {e}")


def _send_alert_async(level: str, title: str, content: str):
    """异步发送告警（安全方式）"""
    try:
        from src.services.alert import alert_service

        # 只有在服务已初始化时才发送告警，避免在日志处理中触发数据库操作
        if not alert_service._initialized:
            return

        # 尝试获取当前运行的事件循环
        try:
            loop = asyncio.get_running_loop()
            # 在异步上下文中，使用 call_soon_threadsafe 安全地调度任务
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(_do_send_alert_safe(alert_service, level, title, content))
            )
        except RuntimeError:
            # 不在异步上下文中，跳过发送（避免创建新事件循环导致冲突）
            pass
    except Exception:
        pass  # 静默失败，不影响主程序


async def _do_send_alert_safe(alert_service, level: str, title: str, content: str):
    """安全地执行告警发送（不触发数据库操作）"""
    try:
        # 直接使用 alert_manager 发送，跳过数据库初始化
        from src.services.alert.alert_manager import alert_manager

        message = f"{content}"
        alert_manager.send_alert(message, level.upper())
    except Exception:
        pass  # 静默失败
