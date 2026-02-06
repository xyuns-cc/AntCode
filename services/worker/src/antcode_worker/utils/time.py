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


def format_duration(ms: float) -> str:
    """格式化持续时间"""
    if ms < 1000:
        return f"{ms:.0f}ms"
    elif ms < 60000:
        return f"{ms/1000:.1f}s"
    elif ms < 3600000:
        return f"{ms/60000:.1f}m"
    else:
        return f"{ms/3600000:.1f}h"


def elapsed_since(start: datetime) -> float:
    """计算从 start 到现在的毫秒数"""
    return (datetime.now() - start).total_seconds() * 1000
