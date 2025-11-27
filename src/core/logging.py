"""日志配置"""
import os
import sys
from loguru import logger

from src.core.config import settings


def setup_logging():
    logger.remove()
    
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=settings.LOG_LEVEL,
        colorize=True,
    )
    
    if settings.LOG_TO_FILE:
        log_dir = os.path.dirname(settings.LOG_FILE_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        
        logger.add(
            settings.LOG_FILE_PATH,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level=settings.LOG_LEVEL,
            rotation="500 MB",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
        )
        logger.info(f"日志文件: {settings.LOG_FILE_PATH}")
    
    logger.info(f"日志系统已初始化: 级别={settings.LOG_LEVEL}")
