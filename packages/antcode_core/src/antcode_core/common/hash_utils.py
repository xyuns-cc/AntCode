"""
哈希计算工具模块

提供统一的文件和内容哈希计算功能，支持 MD5 和 SHA256 算法。

Requirements: 8.2
"""

import hashlib
from typing import Literal

HashAlgorithm = Literal["md5", "sha256"]

# 默认读取块大小（8KB）
DEFAULT_CHUNK_SIZE = 8192


def calculate_content_hash(
    content: bytes | str,
    algorithm: HashAlgorithm = "md5",
) -> str:
    """
    计算内容的哈希值

    Args:
        content: 待计算哈希的内容，可以是 bytes 或 str
        algorithm: 哈希算法，支持 "md5" 或 "sha256"

    Returns:
        内容的十六进制哈希值

    Raises:
        ValueError: 不支持的哈希算法
    """
    if algorithm not in ("md5", "sha256"):
        raise ValueError(f"不支持的哈希算法: {algorithm}，仅支持 md5 和 sha256")

    if isinstance(content, str):
        content = content.encode("utf-8")

    return hashlib.new(algorithm, content).hexdigest()
