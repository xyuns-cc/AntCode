"""读取当前权威 Master 代际，供不持有 Leader 身份的派发入口开栅栏。

B12: ``publish_ready_batch`` 已经要求必传 Master 代际。Master 自己的 loop
能直接拿到本进程的 Leader fencing token；但 Web API 手动派发、core 内嵌
调度器、爬虫批次派发这三类入口并不持有 Leader 身份，它们唯一正确的代际
来源，是 Master 在 Leader 激活时写进 ``scheduler_authority`` 单行表的那个
权威代际（见 ``antcode_master.control.scheduler_authority``）。

读不到活跃权威 = 集群里没有任何 Master 完成过收敛。此时放行派发等于绕过
Master 栅栏、把任务写进 ready stream 却没人负责收敛，因此这里 fail-closed
直接报错，不做任何降级。

分层说明：``services/web_api`` 与 ``packages/antcode_core`` 都不能 import
``services/master``，所以读取接口下沉到 core，与它包装的栅栏原语
``lease_fenced_ready_publish`` 同层同目录。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from antcode_core.application.services.lease_fenced_ready_publish import (
    MIN_GENERATION,
    SchedulerDispatchFenceError,
    scheduler_dispatch_epoch,
)
from antcode_core.domain.models.scheduler_authority import (
    SCHEDULER_AUTHORITY_NAME,
    SchedulerAuthority,
)


class SchedulerAuthorityUnavailableError(SchedulerDispatchFenceError):
    """``scheduler_authority`` 里没有可用的 Master 代际，派发必须被拒绝。

    继承 ``SchedulerDispatchFenceError``，这样既有的"栅栏类异常一律上抛、
    不得吞成软失败"的处理路径会自动把它当成不可降级错误。
    """


async def require_active_scheduler_epoch() -> int:
    """读取当前权威 Master 代际；没有活跃权威时显式失败。"""
    authority = await SchedulerAuthority.filter(name=SCHEDULER_AUTHORITY_NAME).first()
    if authority is None:
        raise SchedulerAuthorityUnavailableError(
            f"派发被拒绝：scheduler_authority 表没有 name={SCHEDULER_AUTHORITY_NAME!r} 的记录，"
            "说明集群内没有任何 Master 完成过 Leader 激活。"
            "没有 Master 代际就无法给 ready stream 上栅栏，派发会绕过 Master 收敛，故拒绝。"
        )
    token = int(authority.fencing_token)
    if token < MIN_GENERATION:
        raise SchedulerAuthorityUnavailableError(
            f"派发被拒绝：scheduler_authority.fencing_token={token} 非法"
            f"（必须 >= {MIN_GENERATION}），权威代际记录已损坏，无法据此派发。"
        )
    return token


@contextlib.asynccontextmanager
async def active_scheduler_dispatch_epoch() -> AsyncIterator[int]:
    """在当前权威 Master 代际下打开一次派发调用树的 Redis 栅栏。"""
    token = await require_active_scheduler_epoch()
    with scheduler_dispatch_epoch(token):
        yield token


__all__ = [
    "SchedulerAuthorityUnavailableError",
    "active_scheduler_dispatch_epoch",
    "require_active_scheduler_epoch",
]
