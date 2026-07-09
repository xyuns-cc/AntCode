"""异步重试工具：指数退避 + 随机 jitter。

轻量替代 tenacity —— 只依赖标准库。适用于 Redis/DB 短抖动、幂等 HTTP GET 等场景。
不适用于修改类操作（POST/PUT/DELETE），除非调用方确认幂等。
"""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from loguru import logger

P = ParamSpec("P")
R = TypeVar("R")


class RetryError(Exception):
    """所有重试都失败后抛出，包装最后一次的原始异常。"""

    def __init__(self, attempts: int, last_error: BaseException):
        super().__init__(f"重试 {attempts} 次后仍失败: {last_error!r}")
        self.attempts = attempts
        self.last_error = last_error


def _compute_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    """指数退避 + [0.5, 1.5) 倍随机 jitter，避免雪崩。"""
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return delay * (0.5 + random.random())


def async_retry(
    *,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    log_prefix: str = "",
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """异步函数重试装饰器。

    Args:
        exceptions: 要重试的异常类型（默认所有 Exception，一般应窄化到网络类）
        max_attempts: 最大尝试次数（包含首次），默认 3
        base_delay: 首次退避秒数，默认 0.5s（下一次 1s、2s...）
        max_delay: 单次退避上限，默认 10s
        log_prefix: 日志前缀，方便定位

    Note:
        ``asyncio.CancelledError`` 永远不重试，直接向上抛。
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_error: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except exceptions as exc:
                    last_error = exc
                    if attempt >= max_attempts:
                        break
                    delay = _compute_backoff(attempt, base_delay, max_delay)
                    logger.warning(
                        "{}第 {}/{} 次失败: {!r}，{:.2f}s 后重试",
                        f"[{log_prefix}] " if log_prefix else "",
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
            assert last_error is not None
            raise RetryError(max_attempts, last_error) from last_error

        return wrapper

    return decorator


async def sleep_with_backoff(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> None:
    """无装饰器场景下手动 sleep 的辅助：主循环 while True 内的 fallback。

    Args:
        attempt: 连续失败次数（从 1 开始）
        base_delay: 首次退避秒数
        max_delay: 上限
    """
    delay = _compute_backoff(attempt, base_delay, max_delay)
    await asyncio.sleep(delay)
