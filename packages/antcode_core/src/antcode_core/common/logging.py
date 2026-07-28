"""共享 Loguru 配置，统一启用敏感信息脱敏和 trace id。"""

from __future__ import annotations

import os
import sys

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.log_sanitization import (
    DEFAULT_SENSITIVE_KEY_TOKENS,
    SENSITIVE_PATTERNS,
    SanitizingFilter,
    sanitize_dict,
    sanitize_log_message,
)

CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>trace={extra[trace_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | trace={extra[trace_id]} | {name}:{function}:{line} - {message}"
)

__all__ = [
    "DEFAULT_SENSITIVE_KEY_TOKENS",
    "SENSITIVE_PATTERNS",
    "SanitizingFilter",
    "get_logger",
    "sanitize_dict",
    "sanitize_log_message",
    "setup_logging",
]


def setup_logging(
    level: str | None = None,
    log_to_file: bool | None = None,
    log_file_path: str | None = None,
) -> None:
    """初始化禁用局部变量诊断且统一脱敏的日志 sink。"""
    logger.remove()
    log_level = level or settings.LOG_LEVEL
    should_log_to_file = log_to_file if log_to_file is not None else settings.LOG_TO_FILE
    file_path = log_file_path or settings.LOG_FILE_PATH
    sanitizing_filter = SanitizingFilter()
    logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=log_level,
        colorize=True,
        filter=sanitizing_filter,
        backtrace=False,
        diagnose=False,
    )
    if should_log_to_file:
        _add_file_sink(file_path, log_level, sanitizing_filter)
    logger.info(f"日志初始化完成: level={log_level}, file={should_log_to_file}, sanitize=True")


def _add_file_sink(file_path: str, log_level: str, sanitizing_filter: SanitizingFilter) -> None:
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
        enqueue=True,
        filter=sanitizing_filter,
        backtrace=False,
        diagnose=False,
    )


def get_logger(name: str | None = None):
    """获取共享 logger，可选绑定模块名。"""
    if name:
        return logger.bind(name=name)
    return logger
