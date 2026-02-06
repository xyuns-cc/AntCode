"""
ID 生成工具

Requirements: 13.1
"""

import os
import time
import uuid
from threading import Lock

# 序列号生成器
_seq_lock = Lock()
_seq_counter = 0
_seq_timestamp = 0


def generate_run_id(prefix: str = "run") -> str:
    """
    生成运行 ID

    格式: {prefix}-{timestamp}-{random}
    """
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:8]
    return f"{prefix}-{ts}-{rand}"


def generate_trace_id() -> str:
    """
    生成追踪 ID

    格式: 32 位十六进制字符串
    """
    return uuid.uuid4().hex


def generate_seq() -> int:
    """
    生成本地序列号（单调递增）

    每秒重置，保证同一秒内单调递增
    """
    global _seq_counter, _seq_timestamp

    with _seq_lock:
        current_ts = int(time.time())
        if current_ts != _seq_timestamp:
            _seq_timestamp = current_ts
            _seq_counter = 0
        _seq_counter += 1
        return _seq_counter


def generate_worker_id() -> str:
    """
    生成 Worker ID

    基于主机名和进程 ID
    """
    import socket
    hostname = socket.gethostname()[:8]
    pid = os.getpid()
    rand = uuid.uuid4().hex[:4]
    return f"w-{hostname}-{pid}-{rand}"
