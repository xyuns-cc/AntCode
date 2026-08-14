"""B12: Web API 手动派发的 Master 代际栅栏（HTTP 边界的失败翻译）。

手动派发不持有 Leader 身份，代际只能取自权威 ``scheduler_authority``。
没有活跃 Master 权威时以 503 显式拒绝，而不是无栅栏把任务写进 ready
stream —— 那样的任务没有 Master 负责收敛，属于坏派发。

这里只做"栅栏获取失败 -> HTTP 503"的翻译；代际的读取与生效仍由 core 的
``require_active_scheduler_epoch`` / ``scheduler_dispatch_epoch`` 负责。
翻译只包住栅栏获取本身，调用树内部再抛出的栅栏异常保持原样上抛。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from antcode_core.application.services.lease_fenced_ready_publish import scheduler_dispatch_epoch
from antcode_core.application.services.scheduler_authority_epoch import (
    SchedulerAuthorityUnavailableError,
    require_active_scheduler_epoch,
)
from fastapi import HTTPException, status


@contextlib.asynccontextmanager
async def http_fenced_dispatch_epoch() -> AsyncIterator[int]:
    """在权威 Master 代际下打开一次手动派发；取不到权威时 503 拒绝。"""
    try:
        epoch = await require_active_scheduler_epoch()
    except SchedulerAuthorityUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    with scheduler_dispatch_epoch(epoch):
        yield epoch


__all__ = ["http_fenced_dispatch_epoch"]
