import os
import sys
from pathlib import Path
from loguru import logger
from spider.spider import settings


class LoguruLogger:
    """全局日志管理器"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._initialized = True
            self.setup_logger()
    
    def setup_logger(self):
        """配置logger"""
        # 如果未启用日志，直接返回
        if not getattr(settings, 'LOG_ENABLED', True):
            logger.disable("spider")
            return
        
        # 移除默认的handler
        logger.remove()
        
        # 获取配置
        log_level = getattr(settings, 'LOG_LEVEL', 'INFO')
        log_format = getattr(settings, 'LOG_FORMAT', 
                           "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                           "<level>{level: <8}</level> | "
                           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                           "<level>{message}</level>")
        log_colorize = getattr(settings, 'LOG_COLORIZE', True)
        log_serialize = getattr(settings, 'LOG_SERIALIZE', False)
        log_backtrace = getattr(settings, 'LOG_BACKTRACE', True)
        log_diagnose = getattr(settings, 'LOG_DIAGNOSE', True)
        log_enqueue = getattr(settings, 'LOG_ENQUEUE', True)
        
        # 控制台输出
        logger.add(
            sys.stderr,
            format=log_format,
            level=log_level,
            colorize=log_colorize,
            serialize=log_serialize,
            backtrace=log_backtrace,
            diagnose=log_diagnose,
            enqueue=log_enqueue
        )
        
        # 文件输出配置
        log_dir = getattr(settings, 'LOG_DIR', 'logs')
        log_file_name = getattr(settings, 'LOG_FILE_NAME', 'spider_{time:YYYY-MM-DD}.log')
        log_error_file_name = getattr(settings, 'LOG_ERROR_FILE_NAME', 'spider_error_{time:YYYY-MM-DD}.log')
        log_rotation = getattr(settings, 'LOG_ROTATION', '500 MB')
        log_retention = getattr(settings, 'LOG_RETENTION', '10 days')
        log_compression = getattr(settings, 'LOG_COMPRESSION', 'zip')
        log_encoding = getattr(settings, 'LOG_ENCODING', 'utf-8')
        
        # 创建日志目录
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # 所有日志文件
        logger.add(
            log_path / log_file_name,
            format=log_format,
            level=log_level,
            rotation=log_rotation,
            retention=log_retention,
            compression=log_compression,
            encoding=log_encoding,
            serialize=log_serialize,
            backtrace=log_backtrace,
            diagnose=log_diagnose,
            enqueue=log_enqueue
        )
        
        # 错误日志文件（只记录ERROR及以上级别）
        logger.add(
            log_path / log_error_file_name,
            format=log_format,
            level="ERROR",
            rotation=log_rotation,
            retention=log_retention,
            compression=log_compression,
            encoding=log_encoding,
            serialize=log_serialize,
            backtrace=log_backtrace,
            diagnose=log_diagnose,
            enqueue=log_enqueue
        )
        
        logger.success("日志系统初始化成功")
    
    @staticmethod
    def get_logger():
        """获取logger实例"""
        return logger


# 创建全局日志实例
_logger_instance = LoguruLogger()

# 导出logger供其他模块使用
logger = _logger_instance.get_logger()


# 提供便捷的日志装饰器
def log_execution(func):
    """装饰器：记录函数执行"""
    def wrapper(*args, **kwargs):
        logger.debug(f"开始执行函数: {func.__name__}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"函数执行成功: {func.__name__}")
            return result
        except Exception as e:
            logger.error(f"函数执行失败: {func.__name__}, 错误: {str(e)}")
            raise
    return wrapper


def log_error(func):
    """装饰器：记录函数错误"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"函数 {func.__name__} 执行出错: {str(e)}")
            raise
    return wrapper


# 设置项目根目录的logger
def setup_project_logger(project_name="spider"):
    """为整个项目设置logger"""
    logger.configure(extra={"project": project_name})
    return logger


# 初始化项目logger
setup_project_logger()