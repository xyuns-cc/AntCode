"""续期循环终止的监督。

``HeartbeatReporter._loop`` 对 ``LeaseRenewalWindowError`` 是**故意** raise 的——
但异常抛进一个没人监听的 ``asyncio.Task`` 等于 fail-silent：``_running`` 仍为
True、``is_running`` 继续撒谎、Worker 照常执行任务，而租约已经停止续期，master
会把同一个 run 补派给别人（双执行）。

fail-loud 没有接收端就是 fail-silent。本模块负责把"循环终止"这件事翻译成
一个必须有人接住的致命错误。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from loguru import logger


def classify_loop_termination(task: asyncio.Task) -> BaseException | None:
    """把续期任务的终止归类成致命错误；``None`` 表示是正常取消。

    正常返回同样算致命——续期循环只应因 ``stop()`` 取消而结束，自行 return
    意味着 ``_running`` 被意外清零，租约同样不再续。
    """
    if task.cancelled():
        return None
    return task.exception() or RuntimeError("lease 续期循环意外退出")


def report_fatal_termination(
    error: BaseException,
    handler: Callable[[BaseException], object] | None,
) -> None:
    """上报致命终止；没有接收端时必须显式指出这是接线缺陷。"""
    logger.critical(f"lease 续期循环终止，Worker 必须停机避免双执行: {error!r}")
    if handler is None:
        logger.error("未注册致命错误通道，续期终止无法触发停机——这是接线缺陷")
        return
    handler(error)


__all__ = ["classify_loop_termination", "report_fatal_termination"]
