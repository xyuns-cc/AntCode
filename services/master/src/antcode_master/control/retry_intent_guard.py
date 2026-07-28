"""Retry intent 的数据结构与同事务校验/消费原语。

TaskRun.next_retry_at 是 durable retry intent 的唯一权威标记；Redis pending
ZSet 只是投递通道。scheduler_loop 在创建 retry run 的同一事务里依次执行：

1. ``validate_retry_source``：source 行锁（SELECT ... FOR UPDATE）下校验
   intent 仍有效（未被取消 / 未被消费 / 代际匹配）；
2. ``consume_retry_intent``（P1-FN-05）：同事务清除 ``next_retry_at`` ——
   此前 retry_loop 在事务提交（source 行锁释放）之后才清，窗口内并发取消
   端点（retry.py 按 ``next_retry_at__not_isnull`` CAS）会命中 1 行并对外
   报成功，但新 run 已在派发（claim 穿透）。合并进同一事务后语义收敛为：
   取消要么先赢（intent 先清 → ``validate_retry_source`` 判 intent 失效
   丢弃本次 claim），要么明确输（取消 CAS 0 行 → 409）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun


@dataclass(frozen=True)
class RetryIntent:
    task_id: int
    source_run_id: str
    retry_count: int
    retry_time: datetime


@dataclass(frozen=True)
class RetryExecutionOptions:
    source_run_id: str
    retry_count: int
    run_id: str


class RetryIntentInvalidError(RuntimeError):
    """retry intent 永久无效:source run 不存在 / intent 已被消费或失效。

    G3: 与瞬态失败区分开 —— retry_loop 捕获此异常后直接丢弃 payload,
    而不是无限 requeue。继承 RuntimeError 保持既有 except 兼容。
    """


async def validate_retry_source(conn, task_id: int, options: RetryExecutionOptions) -> None:
    """source 行锁下校验 durable intent 仍有效；无效时抛 RetryIntentInvalidError。"""
    source = await TaskRun.filter(run_id=options.source_run_id).using_db(conn).select_for_update().first()
    if source is None or source.task_id != task_id:
        raise RetryIntentInvalidError(f"retry source 无效: run_id={options.source_run_id}")
    # P1-FN-01: 用户取消待重试后 source 会被置 CANCELLED（且清
    # next_retry_at）；两个条件都显式校验，防止任何一条路径漏清。
    if source.status == TaskStatus.CANCELLED:
        raise RetryIntentInvalidError(f"retry source 已被取消: run_id={options.source_run_id}")
    if source.next_retry_at is None or source.retry_count != options.retry_count:
        raise RetryIntentInvalidError(f"retry intent 已失效: run_id={options.source_run_id}")


async def consume_retry_intent(conn, options: RetryExecutionOptions) -> None:
    """P1-FN-05: 与新 run 创建同事务清除 durable intent（语义见模块注释）。"""
    cleared = (
        await TaskRun.filter(
            run_id=options.source_run_id,
            next_retry_at__not_isnull=True,
        )
        .using_db(conn)
        .update(next_retry_at=None)
    )
    if cleared != 1:
        # validate_retry_source 已在同事务行锁下确认 intent 有效，
        # 此处 0 行属于不变量破坏，显式失败而非静默继续。
        raise RetryIntentInvalidError(f"retry intent 清除失败(已被并发消费): run_id={options.source_run_id}")


__all__ = [
    "RetryExecutionOptions",
    "RetryIntent",
    "RetryIntentInvalidError",
    "consume_retry_intent",
    "validate_retry_source",
]
