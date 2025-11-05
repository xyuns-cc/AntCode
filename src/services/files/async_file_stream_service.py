"""
异步文件流式读取服务
提供高效的大文件读取和处理功能
"""

import asyncio
import os
from datetime import datetime, timezone

import aiofiles
from loguru import logger


class AsyncFileStreamService:
    """异步文件流式读取服务"""
    
    def __init__(self, chunk_size = 8192, max_file_size = 100 * 1024 * 1024):
        self.chunk_size = chunk_size
        self.max_file_size = max_file_size  # 100MB限制
        
    async def stream_file_content(self, file_path, start_pos = 0, 
                                chunk_size = None):
        """
        流式读取文件内容
        
        Args:
            file_path: 文件路径
            start_pos: 开始位置
            chunk_size: 块大小
            
        Yields:
            str: 文件内容块
        """
        chunk_size = chunk_size or self.chunk_size
        
        try:
            if not os.path.exists(file_path):
                logger.warning(f"文件不存在: {file_path}")
                return
            
            file_size = os.path.getsize(file_path)
            if file_size > self.max_file_size:
                logger.warning(f"文件过大: {file_size} bytes, 限制: {self.max_file_size} bytes")
                
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                await f.seek(start_pos)
                
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
                    
        except Exception as e:
            logger.error(f"流式读取文件失败 {file_path}: {e}")
            raise
    
    async def stream_file_lines(self, file_path, max_lines = None,
                              reverse = False):
        """
        按行流式读取文件
        
        Args:
            file_path: 文件路径
            max_lines: 最大行数
            reverse: 是否倒序读取（读取最后N行）
            
        Yields:
            str: 文件行内容
        """
        try:
            if not os.path.exists(file_path):
                return
                
            if reverse and max_lines:
                # 倒序读取最后N行
                lines = await self._read_last_n_lines(file_path, max_lines)
                for line in lines:
                    yield line
            else:
                # 正序流式读取
                line_count = 0
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    async for line in f:
                        if max_lines and line_count >= max_lines:
                            break
                        yield line.rstrip('\n\r')
                        line_count += 1
                        
        except Exception as e:
            logger.error(f"按行流式读取失败 {file_path}: {e}")
            raise
    
    async def _read_last_n_lines(self, file_path, n):
        """高效读取文件最后N行"""
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                # 移动到文件末尾
                await f.seek(0, 2)
                file_size = await f.tell()
                
                if file_size == 0:
                    return []
                
                # 从文件末尾向前读取
                buffer_size = min(8192, file_size)
                lines_found = []
                pos = file_size
                buffer = b''
                
                while len(lines_found) < n and pos > 0:
                    # 计算读取位置
                    read_size = min(buffer_size, pos)
                    pos -= read_size
                    
                    await f.seek(pos)
                    chunk = await f.read(read_size)
                    buffer = chunk + buffer
                    
                    # 按行分割
                    lines = buffer.split(b'\n')
                    
                    # 保留第一行（可能不完整）作为下次的buffer
                    if pos > 0:
                        buffer = lines[0]
                        lines = lines[1:]
                    else:
                        buffer = b''
                    
                    # 添加完整的行（倒序）
                    for line in reversed(lines):
                        if line.strip():  # 跳过空行
                            lines_found.insert(0, line.decode('utf-8', errors='ignore'))
                            if len(lines_found) >= n:
                                break
                
                return lines_found[-n:] if len(lines_found) > n else lines_found
                
        except Exception as e:
            logger.error(f"读取最后N行失败 {file_path}: {e}")
            return []
    
    async def get_file_tail(self, file_path, lines = 100):
        """
        获取文件尾部内容（类似tail命令）
        
        Args:
            file_path: 文件路径
            lines: 行数
            
        Returns:
            str: 文件尾部内容
        """
        try:
            tail_lines = await self._read_last_n_lines(file_path, lines)
            return '\n'.join(tail_lines)
        except Exception as e:
            logger.error(f"获取文件尾部失败 {file_path}: {e}")
            return ""
    
    async def monitor_file_changes(self, file_path, 
                                 callback,
                                 poll_interval = 1.0):
        """
        监控文件变化并调用回调
        
        Args:
            file_path: 文件路径
            callback: 变化回调函数
            poll_interval: 轮询间隔（秒）
        """
        last_pos = 0
        last_size = 0
        
        if os.path.exists(file_path):
            last_size = os.path.getsize(file_path)
            last_pos = last_size
        
        try:
            while True:
                await asyncio.sleep(poll_interval)
                
                if not os.path.exists(file_path):
                    continue
                    
                current_size = os.path.getsize(file_path)
                
                if current_size > last_pos:
                    # 文件有新内容
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                        await f.seek(last_pos)
                        new_content = await f.read(current_size - last_pos)
                        
                        if new_content:
                            await callback(new_content)
                    
                    last_pos = current_size
                elif current_size < last_size:
                    # 文件被截断或重新创建
                    last_pos = 0
                    logger.info(f"文件被重置: {file_path}")
                
                last_size = current_size
                
        except asyncio.CancelledError:
            logger.info(f"文件监控已取消: {file_path}")
        except Exception as e:
            logger.error(f"文件监控异常 {file_path}: {e}")
    
    async def search_in_file(self, file_path, pattern, 
                           case_sensitive = False, 
                           max_matches = 1000):
        """
        在文件中搜索模式
        
        Args:
            file_path: 文件路径
            pattern: 搜索模式
            case_sensitive: 是否区分大小写
            max_matches: 最大匹配数
            
        Returns:
            list: 匹配结果列表
        """
        matches = []
        
        try:
            if not case_sensitive:
                pattern = pattern.lower()
            
            line_number = 0
            async for line in self.stream_file_lines(file_path):
                line_number += 1
                search_line = line if case_sensitive else line.lower()
                
                if pattern in search_line:
                    matches.append({
                        'line_number': line_number,
                        'content': line,
                        'position': search_line.find(pattern)
                    })
                    
                    if len(matches) >= max_matches:
                        break
            
            return matches
            
        except Exception as e:
            logger.error(f"文件搜索失败 {file_path}: {e}")
            return []
    
    async def get_file_stats(self, file_path):
        """
        获取文件统计信息
        
        Args:
            file_path: 文件路径
            
        Returns:
            dict: 文件统计信息
        """
        try:
            if not os.path.exists(file_path):
                return {
                    'exists': False,
                    'size': 0,
                    'lines': 0,
                    'created': None,
                    'modified': None,
                    'readable': False
                }
            
            stat = os.stat(file_path)
            
            # 计算行数
            line_count = 0
            async for _ in self.stream_file_lines(file_path):
                line_count += 1
            
            return {
                'exists': True,
                'size': stat.st_size,
                'lines': line_count,
                'created': datetime.fromtimestamp(stat.st_ctime, timezone.utc),
                'modified': datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                'readable': os.access(file_path, os.R_OK),
                'size_mb': round(stat.st_size / 1024 / 1024, 2)
            }
            
        except Exception as e:
            logger.error(f"获取文件统计失败 {file_path}: {e}")
            return {
                'exists': False,
                'size': 0,
                'lines': 0,
                'error': str(e)
            }


# 创建全局实例
file_stream_service = AsyncFileStreamService()