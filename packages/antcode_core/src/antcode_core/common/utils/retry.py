"""异步重试工具：指数退避 + 随机 jitter。

轻量替代 tenacity —— 只依赖标准库。适用于 Redis/DB 短抖动、幂等 HTTP GET 等场景。
不适用于修改类操作（POST/PUT/DELETE），除非调用方确认幂等。
"""

from __future__ import annotations

import asyncio
import random
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def _compute_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    """指数退避 + [0.5, 1.5) 倍随机 jitter，避免雪崩。"""
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return delay * (0.5 + random.random())


async def sleep_with_backoff(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> None:
    """无装饰器场景下手动 sleep 的辅助：主循环 while True 内的 fallback。

    Args:
        attempt: 连续失败次数（从 1 开始）
        base_delay: 首次退避秒数
        max_delay: 上限
    """
    delay = _compute_backoff(attempt, base_delay, max_delay)
    await asyncio.sleep(delay)
