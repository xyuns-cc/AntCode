"""
异步文件流服务
提供大文件的流式读取、搜索等功能
"""

import os
import re
from collections import deque
from datetime import UTC, datetime

import aiofiles
from loguru import logger


class AsyncFileStreamService:
    """异步文件流服务类"""

    def __init__(self):
        # 默认块大小 64KB，适合日志文件读取
        self.chunk_size = 64 * 1024
        # 行缓冲区大小
        self.line_buffer_size = 1024

    async def stream_file_content(self, file_path, chunk_size=None):
        """
        流式读取文件内容

        Args:
            file_path: 文件路径
            chunk_size: 块大小，默认使用实例配置

        Yields:
            str: 文件内容块
        """
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return

        chunk_size = chunk_size or self.chunk_size

        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except UnicodeDecodeError:
            # 尝试使用 latin-1 编码读取
            logger.warning(f"UTF-8 解码失败，尝试 latin-1: {file_path}")
            async with aiofiles.open(file_path, encoding="latin-1") as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except Exception as e:
            logger.error(f"流式读取文件失败 {file_path}: {e}")
            raise

    async def stream_file_lines(self, file_path, max_lines=None):
        """
        按行流式读取文件

        Args:
            file_path: 文件路径
            max_lines: 最大行数，None 表示读取所有行

        Yields:
            str: 文件行内容
        """
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return

        line_count = 0

        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                async for line in f:
                    yield line.rstrip("\n\r")
                    line_count += 1
                    if max_lines and line_count >= max_lines:
                        break
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 解码失败，尝试 latin-1: {file_path}")
            async with aiofiles.open(file_path, encoding="latin-1") as f:
                async for line in f:
                    yield line.rstrip("\n\r")
                    line_count += 1
                    if max_lines and line_count >= max_lines:
                        break
        except Exception as e:
            logger.error(f"按行读取文件失败 {file_path}: {e}")
            raise

    async def get_file_tail(self, file_path, lines=100):
        """
        获取文件最后 N 行

        Args:
            file_path: 文件路径
            lines: 要获取的行数

        Returns:
            str: 文件最后 N 行内容
        """
        if not os.path.exists(file_path):
            return ""

        try:
            # 使用 deque 保持固定大小的行缓冲区
            tail_lines = deque(maxlen=lines)

            async with aiofiles.open(file_path, encoding="utf-8") as f:
                async for line in f:
                    tail_lines.append(line.rstrip("\n\r"))

            return "\n".join(tail_lines)

        except UnicodeDecodeError:
            logger.warning(f"UTF-8 解码失败，尝试 latin-1: {file_path}")
            tail_lines = deque(maxlen=lines)
            async with aiofiles.open(file_path, encoding="latin-1") as f:
                async for line in f:
                    tail_lines.append(line.rstrip("\n\r"))
            return "\n".join(tail_lines)

        except Exception as e:
            logger.error(f"读取文件尾部失败 {file_path}: {e}")
            return f"读取失败: {e}"

    async def search_in_file(self, file_path, pattern, case_sensitive=False, max_matches=1000):
        """
        在文件中搜索

        Args:
            file_path: 文件路径
            pattern: 搜索模式（支持正则表达式）
            case_sensitive: 是否区分大小写
            max_matches: 最大匹配数

        Returns:
            list[dict]: 匹配结果列表，每项包含行号和内容
        """
        if not os.path.exists(file_path):
            return []

        results = []
        flags = 0 if case_sensitive else re.IGNORECASE

        try:
            compiled_pattern = re.compile(pattern, flags)
        except re.error as e:
            logger.error(f"无效的正则表达式 '{pattern}': {e}")
            # 如果正则无效，使用简单字符串匹配
            compiled_pattern = None

        try:
            line_number = 0
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                async for line in f:
                    line_number += 1
                    line_content = line.rstrip("\n\r")

                    # 匹配检查
                    matched = False
                    if compiled_pattern:
                        matched = compiled_pattern.search(line_content) is not None
                    else:
                        # 简单字符串匹配
                        matched = pattern in line_content if case_sensitive else pattern.lower() in line_content.lower()

                    if matched:
                        results.append({"line_number": line_number, "content": line_content})
                        if len(results) >= max_matches:
                            break

        except UnicodeDecodeError:
            logger.warning(f"UTF-8 解码失败，尝试 latin-1: {file_path}")
            line_number = 0
            async with aiofiles.open(file_path, encoding="latin-1") as f:
                async for line in f:
                    line_number += 1
                    line_content = line.rstrip("\n\r")

                    matched = False
                    if compiled_pattern:
                        matched = compiled_pattern.search(line_content) is not None
                    else:
                        matched = pattern in line_content if case_sensitive else pattern.lower() in line_content.lower()

                    if matched:
                        results.append({"line_number": line_number, "content": line_content})
                        if len(results) >= max_matches:
                            break

        except Exception as e:
            logger.error(f"搜索文件失败 {file_path}: {e}")

        return results

    async def get_file_stats(self, file_path):
        """
        获取文件统计信息

        Args:
            file_path: 文件路径

        Returns:
            dict: 文件统计信息
        """
        if not os.path.exists(file_path):
            return {
                "exists": False,
                "size": 0,
                "lines": 0,
                "modified_time": None,
                "error": "文件不存在",
            }

        try:
            # 获取文件基本信息
            stat_result = os.stat(file_path)
            file_size = stat_result.st_size
            modified_time = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)

            # 计算行数（对大文件使用估算）
            if file_size > 100 * 1024 * 1024:  # 大于 100MB
                # 估算行数：读取前 1MB 统计平均行长度
                line_count = await self._estimate_line_count(file_path, file_size)
            else:
                line_count = await self._count_lines(file_path)

            return {
                "exists": True,
                "size": file_size,
                "size_human": self._format_size(file_size),
                "lines": line_count,
                "modified_time": modified_time.isoformat(),
            }

        except Exception as e:
            logger.error(f"获取文件信息失败 {file_path}: {e}")
            return {
                "exists": True,
                "size": 0,
                "lines": 0,
                "modified_time": None,
                "error": str(e),
            }

    async def _count_lines(self, file_path):
        """计算文件行数"""
        count = 0
        try:
            async with aiofiles.open(file_path, "rb") as f:
                while True:
                    chunk = await f.read(self.chunk_size)
                    if not chunk:
                        break
                    count += chunk.count(b"\n")
        except Exception as e:
            logger.error(f"计算行数失败 {file_path}: {e}")
        return count

    async def _estimate_line_count(self, file_path, file_size):
        """估算大文件行数"""
        try:
            sample_size = 1024 * 1024  # 1MB 样本
            async with aiofiles.open(file_path, "rb") as f:
                sample = await f.read(sample_size)
                if not sample:
                    return 0
                sample_lines = sample.count(b"\n")
                if sample_lines == 0:
                    return 1
                avg_line_length = len(sample) / sample_lines
                return int(file_size / avg_line_length)
        except Exception as e:
            logger.error(f"估算行数失败 {file_path}: {e}")
            return 0

    @staticmethod
    def _format_size(size):
        """格式化文件大小"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"


# 创建全局实例
file_stream_service = AsyncFileStreamService()
