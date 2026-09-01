"""
时间工具

Requirements: 13.2
"""

import time
from datetime import UTC, datetime


def now_ms() -> int:
    """当前时间戳（毫秒）"""
    return int(time.time() * 1000)


def now_iso() -> str:
    """当前时间 ISO 格式"""
    return datetime.now(UTC).isoformat()


def parse_iso(s: str) -> datetime | None:
    """解析 ISO 格式时间"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
