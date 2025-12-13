"""
哈希计算工具模块

提供统一的文件和内容哈希计算功能，支持 MD5 和 SHA256 算法。
独立于 Master，供 Worker 节点使用。
"""

import hashlib
from pathlib import Path
from typing import Literal

from loguru import logger


HashAlgorithm = Literal["md5", "sha256"]

# 默认读取块大小（8KB）
DEFAULT_CHUNK_SIZE = 8192


def calculate_file_hash(
    file_path: str | Path,
    algorithm: HashAlgorithm = "sha256",
    chunk_size: int = DEFAULT_CHUNK_SIZE
) -> str:
    """
    计算文件的哈希值
    
    Args:
        file_path: 文件路径
        algorithm: 哈希算法，支持 "md5" 或 "sha256"
        chunk_size: 读取块大小，默认 8KB
        
    Returns:
        文件的十六进制哈希值
        
    Raises:
        FileNotFoundError: 文件不存在
        PermissionError: 无权限读取文件
        ValueError: 不支持的哈希算法
    """
    if algorithm not in ("md5", "sha256"):
        raise ValueError(f"不支持的哈希算法: {algorithm}，仅支持 md5 和 sha256")

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    hash_func = hashlib.new(algorithm)

    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                hash_func.update(chunk)
        return hash_func.hexdigest()
    except PermissionError as e:
        logger.error(f"无权限读取文件 {file_path}: {e}")
        raise


def calculate_content_hash(
    content: bytes | str,
    algorithm: HashAlgorithm = "md5"
) -> str:
    """
    计算内容的哈希值
    
    Args:
        content: 待计算哈希的内容，可以是 bytes 或 str
        algorithm: 哈希算法，支持 "md5" 或 "sha256"
        
    Returns:
        内容的十六进制哈希值
    """
    if algorithm not in ("md5", "sha256"):
        raise ValueError(f"不支持的哈希算法: {algorithm}，仅支持 md5 和 sha256")

    if isinstance(content, str):
        content = content.encode("utf-8")

    return hashlib.new(algorithm, content).hexdigest()


def verify_file_hash(
    file_path: str | Path,
    expected_hash: str,
    algorithm: HashAlgorithm = "sha256"
) -> bool:
    """
    验证文件哈希值是否匹配
    
    Args:
        file_path: 文件路径
        expected_hash: 期望的哈希值
        algorithm: 哈希算法
        
    Returns:
        哈希值是否匹配
    """
    try:
        actual_hash = calculate_file_hash(file_path, algorithm)
        return actual_hash.lower() == expected_hash.lower()
    except (FileNotFoundError, PermissionError):
        return False


def create_hash_calculator(algorithm: HashAlgorithm = "md5"):
    """
    创建哈希计算器实例
    
    用于流式计算哈希值，适合处理大文件或异步文件读取场景。
    
    Args:
        algorithm: 哈希算法，支持 "md5" 或 "sha256"
        
    Returns:
        hashlib 哈希对象，支持 update() 和 hexdigest() 方法
    """
    if algorithm not in ("md5", "sha256"):
        raise ValueError(f"不支持的哈希算法: {algorithm}，仅支持 md5 和 sha256")

    return hashlib.new(algorithm)
