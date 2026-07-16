"""Run 级 SSE 订阅代理。

每个 SSE 连接持有一个有界 asyncio.Queue，实时消息（ingest stream / worker
HTTP 上报 notifier）按 run_id fan-out 投递。慢消费者队列满时投放溢出哨兵
并停止投递——消费端读到哨兵应结束流，客户端重连后重新拿全量历史（对齐
原 WebSocket 1013 慢消费者语义）。

连接上限沿用原 WebSocket 的 settings（部署面配置兼容）：
- WEBSOCKET_MAX_CONN_PER_EXECUTION（默认 200）
- WEBSOCKET_MAX_TOTAL_CONN（默认 20000）
- WEBSOCKET_MAX_CONN_PER_USER（默认 20）

所有方法都是同步的（检查与变更之间无 await），在 asyncio 单线程模型下
天然原子，无需锁。
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any

from antcode_core.common.config import settings
from loguru import logger

# 队列容量需覆盖历史回放窗口：生成器回放 HISTORY_LIMIT（10000）帧期间不消费
# 队列，期间实时帧全部积压于此。容量过小会造成"回放必溢出 → 重连 → 回放更长
# 更必溢出"的活锁（每帧 dict ~0.5KB，5000 帧 ≈ 2.5MB/慢连接，仅溢出场景短暂驻留）。
QUEUE_MAXSIZE = 5000

# 溢出哨兵：慢消费者队列满时投放，消费端读到后应终止流
QUEUE_OVERFLOW = object()


class StreamLimitExceededError(Exception):
    """订阅数超限。"""


@dataclass
class StreamSubscription:
    subscription_id: int
    run_id: str
    user_id: int
    queue: asyncio.Queue[Any] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAXSIZE),
    )
    overflowed: bool = False


class RunStreamBroker:
    def __init__(self) -> None:
        self._subscriptions: dict[str, dict[int, StreamSubscription]] = {}
        self._user_counts: dict[int, int] = {}
        self._total = 0
        self._id_counter = itertools.count(1)
        self.max_per_run = int(getattr(settings, "WEBSOCKET_MAX_CONN_PER_EXECUTION", 200))
        self.max_total = int(getattr(settings, "WEBSOCKET_MAX_TOTAL_CONN", 20000))
        self.max_per_user = int(getattr(settings, "WEBSOCKET_MAX_CONN_PER_USER", 20))
        # 统计
        self._overflow_count = 0

    # ------------------------------------------------------------------ #
    # 订阅生命周期
    # ------------------------------------------------------------------ #

    def ensure_capacity(self, run_id: str, user_id: int) -> None:
        """容量预检（供路由层在开始流式响应前返回 429）。"""
        if self._total >= self.max_total:
            raise StreamLimitExceededError("服务端日志流连接数已达上限")
        if len(self._subscriptions.get(run_id, {})) >= self.max_per_run:
            raise StreamLimitExceededError("该执行记录的日志流订阅数已达上限")
        if self._user_counts.get(user_id, 0) >= self.max_per_user:
            raise StreamLimitExceededError("当前用户的日志流连接数已达上限")

    def subscribe(self, run_id: str, user_id: int) -> StreamSubscription:
        self.ensure_capacity(run_id, user_id)
        subscription = StreamSubscription(
            subscription_id=next(self._id_counter),
            run_id=run_id,
            user_id=user_id,
        )
        self._subscriptions.setdefault(run_id, {})[subscription.subscription_id] = subscription
        self._user_counts[user_id] = self._user_counts.get(user_id, 0) + 1
        self._total += 1
        return subscription

    def unsubscribe(self, subscription: StreamSubscription) -> None:
        run_subs = self._subscriptions.get(subscription.run_id)
        if not run_subs or subscription.subscription_id not in run_subs:
            return
        run_subs.pop(subscription.subscription_id)
        if not run_subs:
            # 空 run 状态清理，避免 run_id 键累积
            self._subscriptions.pop(subscription.run_id, None)
        remaining = self._user_counts.get(subscription.user_id, 0) - 1
        if remaining > 0:
            self._user_counts[subscription.user_id] = remaining
        else:
            self._user_counts.pop(subscription.user_id, None)
        self._total -= 1

    # ------------------------------------------------------------------ #
    # 投递
    # ------------------------------------------------------------------ #

    def has_subscribers(self, run_id: str) -> bool:
        return bool(self._subscriptions.get(run_id))

    def subscribed_runs(self) -> set[str]:
        return set(self._subscriptions.keys())

    def publish(self, run_id: str, message: dict[str, Any]) -> None:
        """向 run 的所有订阅者投递消息；慢消费者标记溢出并停止投递。"""
        run_subs = self._subscriptions.get(run_id)
        if not run_subs:
            return
        for subscription in list(run_subs.values()):
            if subscription.overflowed:
                continue
            try:
                subscription.queue.put_nowait(message)
            except asyncio.QueueFull:
                subscription.overflowed = True
                self._overflow_count += 1
                # 清空积压后投哨兵：溢出即断的语义要求消费端下一次 get 立即
                # 看到哨兵——若只腾一个槽位把哨兵追加到 FIFO 队尾，慢消费者
                # 还要先拖完几千条陈旧帧（期间新日志全部静默丢弃）才会重连
                _drain(subscription.queue)
                subscription.queue.put_nowait(QUEUE_OVERFLOW)
                logger.warning(
                    "日志流慢消费者，队列溢出断开: run_id={} subscription_id={}",
                    run_id,
                    subscription.subscription_id,
                )

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        return {
            "total_subscriptions": self._total,
            "active_runs": len(self._subscriptions),
            "subscriptions_by_run": {run_id: len(subs) for run_id, subs in self._subscriptions.items()},
            "overflow_disconnects": self._overflow_count,
            "limits": {
                "max_per_run": self.max_per_run,
                "max_total": self.max_total,
                "max_per_user": self.max_per_user,
            },
        }


def _drain(queue: asyncio.Queue[Any]) -> None:
    """清空队列（溢出后陈旧帧已无投递价值，重连全量历史回放会补齐）。"""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


run_stream_broker = RunStreamBroker()

__all__ = [
    "QUEUE_MAXSIZE",
    "QUEUE_OVERFLOW",
    "RunStreamBroker",
    "StreamLimitExceededError",
    "StreamSubscription",
    "run_stream_broker",
]
