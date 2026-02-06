"""ID 生成模块

提供各种 ID 生成功能：
- UUID 生成
- 任务运行 ID 生成
- 短 ID 生成
"""

import secrets
import time
import uuid
from datetime import datetime


def generate_uuid() -> str:
    """生成 UUID4 字符串

    Returns:
        UUID4 字符串（不含连字符）
    """
    return uuid.uuid4().hex


def generate_id(prefix: str = "") -> str:
    """生成带前缀的唯一 ID

    Args:
        prefix: ID 前缀

    Returns:
        格式: {prefix}_{timestamp}_{random}
    """
    timestamp = int(time.time() * 1000)
    random_part = secrets.token_hex(4)
    if prefix:
        return f"{prefix}_{timestamp}_{random_part}"
    return f"{timestamp}_{random_part}"


def generate_run_id(task_id: int | str | None = None) -> str:
    """生成任务运行 ID

    Args:
        task_id: 可选的任务 ID，用于关联

    Returns:
        格式: run_{timestamp}_{random} 或 run_{task_id}_{timestamp}_{random}
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_part = secrets.token_hex(4)
    if task_id:
        return f"run_{task_id}_{timestamp}_{random_part}"
    return f"run_{timestamp}_{random_part}"


def generate_short_id(length: int = 8) -> str:
    """生成短随机 ID

    Args:
        length: ID 长度（字节数的两倍，因为是十六进制）

    Returns:
        十六进制随机字符串
    """
    return secrets.token_hex(length // 2)


def generate_batch_id() -> str:
    """生成批次 ID

    Returns:
        格式: batch_{timestamp}_{random}
    """
    return generate_id("batch")


def generate_worker_id() -> str:
    """生成 Worker ID

    Returns:
        格式: worker_{timestamp}_{random}
    """
    return generate_id("worker")


def generate_session_id() -> str:
    """生成会话 ID

    Returns:
        格式: session_{timestamp}_{random}
    """
    return generate_id("session")
