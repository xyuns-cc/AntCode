"""日志配置模块

提供日志初始化、格式化和敏感信息脱敏功能。
"""
import os
import re
import sys
import asyncio
from typing import Any, Dict

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

# 敏感字段模式（用于脱敏）
SENSITIVE_PATTERNS = [
    # API Key / Secret Key
    (re.compile(r'(api[_-]?key|secret[_-]?key|access[_-]?key)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{8,})["\']?', re.IGNORECASE), r'\1=***REDACTED***'),
    # Password
    (re.compile(r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?([^"\'\s,}]{3,})["\']?', re.IGNORECASE), r'\1=***REDACTED***'),
    # Token
    (re.compile(r'(token|bearer|jwt)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{20,})["\']?', re.IGNORECASE), r'\1=***REDACTED***'),
    # Authorization Header
    (re.compile(r'(Authorization)["\']?\s*[:=]\s*["\']?(Bearer\s+)?([a-zA-Z0-9_\-\.]{20,})["\']?', re.IGNORECASE), r'\1=***REDACTED***'),
    # Database URL with password
    (re.compile(r'(mysql|postgres|postgresql|redis)://([^:]+):([^@]+)@', re.IGNORECASE), r'\1://\2:***@'),
    # Email (部分脱敏)
    (re.compile(r'([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)'), r'***@\2'),
]


def sanitize_log_message(message: str) -> str:
    """
    对日志消息进行敏感信息脱敏
    
    Args:
        message: 原始日志消息
        
    Returns:
        脱敏后的日志消息
    """
    if not message:
        return message
    
    sanitized = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    
    return sanitized


def sanitize_dict(data: Dict[str, Any], sensitive_keys: set = None) -> Dict[str, Any]:
    """
    对字典数据进行敏感信息脱敏
    
    Args:
        data: 原始字典数据
        sensitive_keys: 需要脱敏的键名集合
        
    Returns:
        脱敏后的字典
    """
    if sensitive_keys is None:
        sensitive_keys = {
            'password', 'passwd', 'pwd', 'secret', 'token', 'api_key', 
            'apikey', 'secret_key', 'secretkey', 'access_key', 'accesskey',
            'authorization', 'auth', 'credential', 'credentials'
        }
    
    if not isinstance(data, dict):
        return data
    
    result = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(sk in key_lower for sk in sensitive_keys):
            result[key] = '***REDACTED***'
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value, sensitive_keys)
        elif isinstance(value, str):
            result[key] = sanitize_log_message(value)
        else:
            result[key] = value
    
    return result


class SanitizingFilter:
    """日志脱敏过滤器"""
    
    def __call__(self, record: Dict[str, Any]) -> bool:
        """对日志记录进行脱敏处理"""
        # 脱敏消息内容
        if 'message' in record:
            record['message'] = sanitize_log_message(record['message'])
        
        # 脱敏 extra 字段
        if 'extra' in record and isinstance(record['extra'], dict):
            record['extra'] = sanitize_dict(record['extra'])
        
        return True


def setup_logging() -> None:
    """初始化日志系统，包含敏感信息脱敏"""
    logger.remove()

    # 创建脱敏过滤器
    sanitizing_filter = SanitizingFilter()

    # 控制台输出（带脱敏）
    logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=settings.LOG_LEVEL,
        colorize=True,
        filter=sanitizing_filter,
    )

    # 文件输出（带脱敏）
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
            filter=sanitizing_filter,
        )

    # 添加告警处理器（仅处理 ERROR 和 CRITICAL，带脱敏）
    logger.add(
        _alert_sink,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
        level="ERROR",
        filter=lambda record: sanitizing_filter(record) and record["level"].name in ["ERROR", "CRITICAL"],
        enqueue=True,  # 异步处理，避免阻塞主线程
    )

    logger.info(f"日志初始化完成: level={settings.LOG_LEVEL}, file={settings.LOG_TO_FILE}, sanitize=True")


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
