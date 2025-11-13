# src/core/logging.py
"""日志系统配置模块"""
import os
import sys
from loguru import logger

from src.core.config import settings


def setup_logging():
    """配置日志系统
    
    功能：
    - 配置控制台输出（带颜色格式化）
    - 配置文件输出（支持日志轮转、压缩）
    - 根据环境变量控制日志级别和输出位置
    
    环境变量：
    - LOG_LEVEL: 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
    - LOG_TO_FILE: 是否输出到文件（true/false）
    - LOG_FILE_PATH: 日志文件路径
    """
    # 移除默认的控制台处理器
    logger.remove()
    
    # 添加控制台输出（带颜色）
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=settings.LOG_LEVEL,
        colorize=True,
    )
    
    # 如果启用文件日志，添加文件处理器
    if settings.LOG_TO_FILE:
        # 确保日志目录存在
        log_dir = os.path.dirname(settings.LOG_FILE_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        
        # 添加文件日志（支持日志轮转）
        logger.add(
            settings.LOG_FILE_PATH,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level=settings.LOG_LEVEL,
            rotation="500 MB",  # 单个日志文件最大500MB
            retention="30 days",  # 保留30天
            compression="zip",  # 压缩旧日志
            encoding="utf-8",
        )
        logger.info(f"📝 日志文件已配置: {settings.LOG_FILE_PATH}")
    
    logger.info(f"📋 日志系统初始化完成 - 日志级别: {settings.LOG_LEVEL}")

