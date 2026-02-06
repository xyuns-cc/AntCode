"""日志配置模块

提供日志初始化、格式化和敏感信息脱敏功能。
"""

import os
import re
import sys
from typing import Any

from loguru import logger

from antcode_core.common.config import settings

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
    # API 密钥/私密密钥
    (
        re.compile(
            r'(api[_-]?key|secret[_-]?key|access[_-]?key)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{8,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 密码
    (
        re.compile(
            r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?([^"\'\s,}]{3,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 令牌
    (
        re.compile(
            r'(token|bearer|jwt)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{20,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 授权头
    (
        re.compile(
            r'(Authorization)["\']?\s*[:=]\s*["\']?(Bearer\s+)?([a-zA-Z0-9_\-\.]{20,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 含密码的数据库 URL
    (
        re.compile(r"(mysql|postgres|postgresql|redis)://([^:]+):([^@]+)@", re.IGNORECASE),
        r"\1://\2:***@",
    ),
    # 邮箱（部分脱敏）
    (re.compile(r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)"), r"***@\2"),
]


def sanitize_log_message(message: str) -> str:
    """对日志消息进行敏感信息脱敏"""
    if not message:
        return message

    sanitized = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    return sanitized


def sanitize_dict(data: dict[str, Any], sensitive_keys: set[str] | None = None) -> dict[str, Any]:
    """对字典数据进行敏感信息脱敏"""
    if sensitive_keys is None:
        sensitive_keys = {
            "password",
            "passwd",
            "pwd",
            "secret",
            "token",
            "api_key",
            "apikey",
            "secret_key",
            "secretkey",
            "access_key",
            "accesskey",
            "authorization",
            "auth",
            "credential",
            "credentials",
        }

    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(sk in key_lower for sk in sensitive_keys):
            result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value, sensitive_keys)
        elif isinstance(value, str):
            result[key] = sanitize_log_message(value)
        else:
            result[key] = value

    return result


class SanitizingFilter:
    """日志脱敏过滤器"""

    def __call__(self, record: dict[str, Any]) -> bool:
        """对日志记录进行脱敏处理"""
        # 脱敏消息内容
        if "message" in record:
            record["message"] = sanitize_log_message(record["message"])

        # 脱敏 extra 字段
        if "extra" in record and isinstance(record["extra"], dict):
            record["extra"] = sanitize_dict(record["extra"])

        return True


def setup_logging(
    level: str | None = None,
    log_to_file: bool | None = None,
    log_file_path: str | None = None,
) -> None:
    """初始化日志系统，包含敏感信息脱敏

    Args:
        level: 日志级别，默认使用 settings.LOG_LEVEL
        log_to_file: 是否输出到文件，默认使用 settings.LOG_TO_FILE
        log_file_path: 日志文件路径，默认使用 settings.LOG_FILE_PATH
    """
    logger.remove()

    # 使用参数或配置
    log_level = level or settings.LOG_LEVEL
    should_log_to_file = log_to_file if log_to_file is not None else settings.LOG_TO_FILE
    file_path = log_file_path or settings.LOG_FILE_PATH

    # 创建脱敏过滤器
    sanitizing_filter = SanitizingFilter()

    # 控制台输出（带脱敏）
    logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=log_level,
        colorize=True,
        filter=sanitizing_filter,
    )

    # 文件输出（带脱敏）
    if should_log_to_file:
        log_dir = os.path.dirname(file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        logger.add(
            file_path,
            format=FILE_FORMAT,
            level=log_level,
            rotation="500 MB",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
            enqueue=True,  # 异步写入，提升性能
            filter=sanitizing_filter,
        )

    logger.info(
        f"日志初始化完成: level={log_level}, file={should_log_to_file}, sanitize=True"
    )


def get_logger(name: str | None = None):
    """获取 logger 实例

    Args:
        name: logger 名称，用于区分不同模块的日志

    Returns:
        loguru logger 实例
    """
    if name:
        return logger.bind(name=name)
    return logger
