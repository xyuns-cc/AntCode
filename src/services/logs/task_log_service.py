"""任务日志管理服务"""

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from src.core.config import settings
from src.services.files.async_file_stream_service import file_stream_service


class TaskLogService:
    """任务日志管理服务"""
    
    def __init__(self):
        self.log_dir = Path(settings.TASK_LOG_DIR)
        self.max_log_size = settings.TASK_LOG_MAX_SIZE
        self._ensure_log_directory()
    
    def _ensure_log_directory(self):
        """确保日志目录存在"""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"任务日志目录已准备: {self.log_dir}")
        except Exception as e:
            logger.error(f"创建任务日志目录失败: {e}")
            raise
    
    def generate_log_paths(self, execution_id, task_name):
        """
        生成日志文件路径
        
        Args:
            execution_id: 执行ID
            task_name: 任务名称
            
        Returns:
            包含日志文件路径的字典
        """
        # 使用日期和执行ID创建目录结构
        date_str = datetime.now().strftime("%Y-%m-%d")
        task_log_dir = self.log_dir / date_str / execution_id
        
        # 确保目录存在
        task_log_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成日志文件路径
        log_file_path = task_log_dir / "output.log"
        error_log_path = task_log_dir / "error.log"
        
        return {
            "log_file_path": str(log_file_path),
            "error_log_path": str(error_log_path),
            "log_dir": str(task_log_dir)
        }
    
    async def write_log(self, log_file_path, content, append = True, execution_id = None, add_timestamp = True):
        """
        写入日志内容到文件

        Args:
            log_file_path: 日志文件路径
            content: 日志内容
            append: 是否追加模式
            execution_id: 任务执行ID（保留参数以保持兼容性）
            add_timestamp: 是否添加时间戳
        """
        try:
            mode = "a" if append else "w"

            # 检查文件大小
            if os.path.exists(log_file_path) and os.path.getsize(log_file_path) > self.max_log_size:
                logger.warning(f"日志文件 {log_file_path} 超过最大大小限制")
                return

            # 异步写入日志
            def write_sync():
                with open(log_file_path, mode, encoding='utf-8') as f:
                    if add_timestamp:
                        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                        formatted_content = f"[{timestamp}] {content}\n"
                    else:
                        formatted_content = f"{content}\n"
                    f.write(formatted_content)
                    return formatted_content

            formatted_content = await asyncio.get_event_loop().run_in_executor(None, write_sync)

        except Exception as e:
            logger.error(f"写入日志文件失败 {log_file_path}: {e}")


    async def read_log(self, log_file_path, lines = None):
        """
        读取日志文件内容（优化版本）
        
        Args:
            log_file_path: 日志文件路径
            lines: 读取的行数，None表示读取全部
            
        Returns:
            日志内容
        """
        try:
            if not os.path.exists(log_file_path):
                return ""
            
            # 检查文件大小，决定使用哪种读取方式
            file_size = os.path.getsize(log_file_path)
            
            if file_size > 10 * 1024 * 1024:  # 大于10MB使用流式读取
                logger.info(f"大文件检测到，使用流式读取: {log_file_path} ({file_size} bytes)")
                return await self._stream_read_log(log_file_path, lines)
            else:
                # 小文件使用原有方式
                return await self._legacy_read_log(log_file_path, lines)
                
        except Exception as e:
            logger.error(f"读取日志文件失败 {log_file_path}: {e}")
            return f"读取日志失败: {e}"
    
    async def _stream_read_log(self, log_file_path, lines = None):
        """流式读取日志文件"""
        try:
            if lines:
                # 读取最后N行
                content = await file_stream_service.get_file_tail(log_file_path, lines)
                return content
            else:
                # 流式读取全部内容
                chunks = []
                async for chunk in file_stream_service.stream_file_content(log_file_path):
                    chunks.append(chunk)
                return ''.join(chunks)
                
        except Exception as e:
            logger.error(f"流式读取失败 {log_file_path}: {e}")
            return f"流式读取失败: {e}"
    
    async def _legacy_read_log(self, log_file_path, lines = None):
        """传统方式读取日志文件（小文件）"""
        try:
            def read_sync():
                with open(log_file_path, 'r', encoding='utf-8') as f:
                    if lines is None:
                        return f.read()
                    else:
                        # 读取最后N行
                        all_lines = f.readlines()
                        return ''.join(all_lines[-lines:] if len(all_lines) > lines else all_lines)
            
            content = await asyncio.get_event_loop().run_in_executor(None, read_sync)
            return content
            
        except Exception as e:
            logger.error(f"传统读取失败 {log_file_path}: {e}")
            return f"传统读取失败: {e}"
    
    async def stream_log_lines(self, log_file_path, max_lines = None):
        """
        按行流式读取日志文件
        
        Args:
            log_file_path: 日志文件路径
            max_lines: 最大行数
            
        Yields:
            str: 日志行内容
        """
        async for line in file_stream_service.stream_file_lines(log_file_path, max_lines):
            yield line
    
    async def search_in_log(self, log_file_path, pattern, 
                          case_sensitive = False, 
                          max_matches = 1000):
        """
        在日志文件中搜索
        
        Args:
            log_file_path: 日志文件路径
            pattern: 搜索模式
            case_sensitive: 是否区分大小写
            max_matches: 最大匹配数
            
        Returns:
            匹配结果列表
        """
        return await file_stream_service.search_in_file(
            log_file_path, pattern, case_sensitive, max_matches
        )
    
    async def get_log_info(self, log_file_path):
        """
        获取日志文件信息（优化版本）
        
        Args:
            log_file_path: 日志文件路径
            
        Returns:
            日志文件信息
        """
        try:
            # 使用优化的文件统计服务
            return await file_stream_service.get_file_stats(log_file_path)
            
        except Exception as e:
            logger.error(f"获取日志文件信息失败 {log_file_path}: {e}")
            return {
                "exists": False,
                "size": 0,
                "lines": 0,
                "modified_time": None,
                "error": str(e)
            }
    
    async def cleanup_old_logs(self, retention_days = None):
        """
        清理过期的日志文件
        
        Args:
            retention_days: 保留天数，默认使用配置值
        """
        if retention_days is None:
            retention_days = settings.TASK_LOG_RETENTION_DAYS
        
        try:
            cutoff_time = datetime.now(timezone.utc).timestamp() - (retention_days * 24 * 3600)
            deleted_count = 0
            
            def cleanup_sync():
                nonlocal deleted_count
                for root, dirs, files in os.walk(self.log_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        if os.path.getmtime(file_path) < cutoff_time:
                            os.remove(file_path)
                            deleted_count += 1
                
                # 删除空目录
                for root, dirs, files in os.walk(self.log_dir, topdown=False):
                    for dir_name in dirs:
                        dir_path = os.path.join(root, dir_name)
                        try:
                            os.rmdir(dir_path)  # 只删除空目录
                        except OSError:
                            pass  # 目录不为空，跳过
            
            await asyncio.get_event_loop().run_in_executor(None, cleanup_sync)
            logger.info(f"清理了 {deleted_count} 个过期日志文件")
            
        except Exception as e:
            logger.error(f"清理过期日志失败: {e}")
    
    async def get_execution_logs(self, execution_id):
        """
        获取指定执行ID的所有日志
        
        Args:
            execution_id: 执行ID
            
        Returns:
            包含输出日志和错误日志的字典
        """
        # 查找日志文件
        log_files = []
        for date_dir in self.log_dir.iterdir():
            if date_dir.is_dir():
                execution_dir = date_dir / execution_id
                if execution_dir.exists():
                    log_files.append(execution_dir)
        
        if not log_files:
            return {"output": "", "error": ""}
        
        # 假设只有一个匹配的目录（最新的）
        execution_dir = log_files[0]
        
        output_log = await self.read_log(str(execution_dir / "output.log"))
        error_log = await self.read_log(str(execution_dir / "error.log"))
        
        return {
            "output": output_log,
            "error": error_log
        }


# 创建全局实例
task_log_service = TaskLogService()
