"""Process-local SSE fan-out backed by globally shared capacity leases."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from dataclasses import dataclass, field
from typing import Any

from antcode_core.common.config import settings
from loguru import logger

from antcode_web_api.streams.stream_capacity_limiter import (
    LEASE_LIMIT_RUN,
    LEASE_LIMIT_TOTAL,
    LEASE_LIMIT_USER,
    GlobalStreamLimitExceededError,
    RedisStreamCapacityLimiter,
    StreamCapacityLease,
    StreamCapacityLimiter,
    StreamCapacityLimits,
)

QUEUE_MAXSIZE = int(settings.SSE_QUEUE_MAX_MESSAGES)
QUEUE_MAX_BYTES = int(settings.SSE_QUEUE_MAX_BYTES)
LEASE_RENEWAL_DIVISOR = 3
MIN_RENEWAL_INTERVAL_SECONDS = 1.0

QUEUE_OVERFLOW = object()
QUEUE_CAPACITY_UNAVAILABLE = object()


class StreamLimitExceededError(Exception):
    """The configured global SSE subscription limit was reached."""


class StreamCapacityUnavailableError(RuntimeError):
    """The shared capacity coordinator could not acquire a lease."""


@dataclass
class StreamSubscription:
    subscription_id: int
    run_id: str
    user_id: int
    capacity_lease: StreamCapacityLease
    queue: asyncio.Queue[Any] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAXSIZE),
    )
    overflowed: bool = False
    pending_bytes: int = 0


@dataclass(frozen=True)
class _QueuedMessage:
    message: dict[str, Any]
    size_bytes: int


class RunStreamBroker:
    """Owns local queues while Redis enforces capacity across all replicas."""

    def __init__(self, capacity_limiter: StreamCapacityLimiter) -> None:
        lease_ttl = int(settings.SSE_LEASE_TTL_SECONDS)
        self._capacity_limiter = capacity_limiter
        self._renewal_interval = max(
            lease_ttl / LEASE_RENEWAL_DIVISOR,
            MIN_RENEWAL_INTERVAL_SECONDS,
        )
        self._subscriptions: dict[str, dict[int, StreamSubscription]] = {}
        self._user_counts: dict[int, int] = {}
        self._renewal_tasks: dict[int, asyncio.Task[None]] = {}
        self._total = 0
        self._id_counter = itertools.count(1)
        self.max_per_run = int(settings.SSE_MAX_CONN_PER_EXECUTION)
        self.max_total = int(settings.SSE_MAX_TOTAL_CONN)
        self.max_per_user = int(settings.SSE_MAX_CONN_PER_USER)
        self.max_queue_bytes = int(settings.SSE_QUEUE_MAX_BYTES)
        self._overflow_count = 0

    async def ensure_capacity(self, run_id: str, user_id: int) -> None:
        """Check global capacity before the StreamingResponse starts."""
        try:
            await self._capacity_limiter.ensure_capacity(run_id, user_id, self._limits())
        except GlobalStreamLimitExceededError as exc:
            raise StreamLimitExceededError(_limit_message(exc.dimension)) from exc

    async def subscribe(self, run_id: str, user_id: int) -> StreamSubscription:
        try:
            lease = await self._capacity_limiter.acquire(run_id, user_id, self._limits())
        except GlobalStreamLimitExceededError as exc:
            raise StreamLimitExceededError(_limit_message(exc.dimension)) from exc
        except Exception as exc:
            raise StreamCapacityUnavailableError("日志流容量协调服务不可用") from exc
        subscription = StreamSubscription(
            subscription_id=next(self._id_counter),
            run_id=run_id,
            user_id=user_id,
            capacity_lease=lease,
        )
        self._register_local(subscription)
        self._renewal_tasks[subscription.subscription_id] = asyncio.create_task(
            self._renew_lease(subscription),
        )
        return subscription

    async def unsubscribe(self, subscription: StreamSubscription) -> None:
        if not self._remove_local(subscription):
            return
        renewal = self._renewal_tasks.pop(subscription.subscription_id, None)
        if renewal:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
        await self._capacity_limiter.release(subscription.capacity_lease)

    async def stats(self) -> dict[str, Any]:
        global_total, _, _ = await self._capacity_limiter.counts("__stats__", 0)
        return {
            "total_subscriptions": global_total,
            "scope": "global",
            "local": {
                "total_subscriptions": self._total,
                "active_runs": len(self._subscriptions),
                "subscriptions_by_run": {run_id: len(subs) for run_id, subs in self._subscriptions.items()},
                "pending_bytes": self._pending_bytes(),
                "overflow_disconnects": self._overflow_count,
            },
            "limits": {
                "max_per_run": self.max_per_run,
                "max_total": self.max_total,
                "max_per_user": self.max_per_user,
                "max_queue_messages": QUEUE_MAXSIZE,
                "max_queue_bytes": self.max_queue_bytes,
            },
        }

    def has_subscribers(self, run_id: str) -> bool:
        return bool(self._subscriptions.get(run_id))

    def subscribed_runs(self) -> set[str]:
        return set(self._subscriptions.keys())

    def publish(self, run_id: str, message: dict[str, Any]) -> None:
        run_subs = self._subscriptions.get(run_id)
        if not run_subs:
            return
        size_bytes = _message_size(message)
        for subscription in list(run_subs.values()):
            self._publish_to_subscription(subscription, message, size_bytes)

    async def get_message(self, subscription: StreamSubscription, timeout: float) -> Any:
        item = await asyncio.wait_for(subscription.queue.get(), timeout=timeout)
        if not isinstance(item, _QueuedMessage):
            return item
        subscription.pending_bytes = max(subscription.pending_bytes - item.size_bytes, 0)
        return item.message

    def _publish_to_subscription(
        self,
        subscription: StreamSubscription,
        message: dict[str, Any],
        size_bytes: int,
    ) -> None:
        if subscription.overflowed:
            return
        if subscription.pending_bytes + size_bytes > self.max_queue_bytes:
            self._mark_overflowed(subscription)
            return
        try:
            subscription.queue.put_nowait(_QueuedMessage(message, size_bytes))
            subscription.pending_bytes += size_bytes
        except asyncio.QueueFull:
            self._mark_overflowed(subscription)

    def _register_local(self, subscription: StreamSubscription) -> None:
        run_subs = self._subscriptions.setdefault(subscription.run_id, {})
        run_subs[subscription.subscription_id] = subscription
        self._user_counts[subscription.user_id] = self._user_counts.get(subscription.user_id, 0) + 1
        self._total += 1

    def _remove_local(self, subscription: StreamSubscription) -> bool:
        run_subs = self._subscriptions.get(subscription.run_id)
        if not run_subs or subscription.subscription_id not in run_subs:
            return False
        run_subs.pop(subscription.subscription_id)
        if not run_subs:
            self._subscriptions.pop(subscription.run_id, None)
        remaining = self._user_counts.get(subscription.user_id, 0) - 1
        if remaining > 0:
            self._user_counts[subscription.user_id] = remaining
        else:
            self._user_counts.pop(subscription.user_id, None)
        self._total -= 1
        return True

    async def _renew_lease(self, subscription: StreamSubscription) -> None:
        while True:
            await asyncio.sleep(self._renewal_interval)
            try:
                renewed = await self._capacity_limiter.renew(subscription.capacity_lease)
            except Exception as exc:
                logger.exception(
                    "SSE 全局容量租约续租失败: subscription_id={}: {}",
                    subscription.subscription_id,
                    exc,
                )
                self._terminate_for_capacity_failure(subscription)
                return
            if not renewed:
                logger.error(
                    "SSE 全局容量租约已丢失: subscription_id={}",
                    subscription.subscription_id,
                )
                self._terminate_for_capacity_failure(subscription)
                return

    def _terminate_for_capacity_failure(self, subscription: StreamSubscription) -> None:
        _drain(subscription.queue)
        subscription.pending_bytes = 0
        subscription.queue.put_nowait(QUEUE_CAPACITY_UNAVAILABLE)

    def _mark_overflowed(self, subscription: StreamSubscription) -> None:
        subscription.overflowed = True
        self._overflow_count += 1
        _drain(subscription.queue)
        subscription.pending_bytes = 0
        subscription.queue.put_nowait(QUEUE_OVERFLOW)
        logger.warning(
            "日志流慢消费者，队列溢出断开: run_id={} subscription_id={}",
            subscription.run_id,
            subscription.subscription_id,
        )

    async def shutdown(self) -> None:
        """P2 §4.2: 进程退出时取消续租任务并主动释放全局容量租约。

        此前没有 shutdown 钩子，Redis 里的租约只能等 TTL 自然过期，重启
        窗口内全局容量被幽灵租约占用，可拒绝本应可用的新连接。
        """
        subscriptions = [
            subscription for run_subs in self._subscriptions.values() for subscription in run_subs.values()
        ]
        for subscription in subscriptions:
            try:
                await self.unsubscribe(subscription)
            except Exception:
                logger.exception(
                    "SSE broker shutdown 释放订阅失败: subscription_id={}",
                    subscription.subscription_id,
                )

    def _limits(self) -> StreamCapacityLimits:
        return StreamCapacityLimits(self.max_total, self.max_per_run, self.max_per_user)

    def _pending_bytes(self) -> int:
        return sum(
            subscription.pending_bytes
            for subscriptions in self._subscriptions.values()
            for subscription in subscriptions.values()
        )


def _limit_message(dimension: int) -> str:
    messages = {
        LEASE_LIMIT_TOTAL: "服务端日志流连接数已达上限",
        LEASE_LIMIT_RUN: "该执行记录的日志流订阅数已达上限",
        LEASE_LIMIT_USER: "当前用户的日志流连接数已达上限",
    }
    return messages.get(dimension, "日志流连接数已达上限")


def _message_size(message: dict[str, Any]) -> int:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    return len(encoded.encode("utf-8"))


def _drain(queue: asyncio.Queue[Any]) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


run_stream_broker = RunStreamBroker(
    RedisStreamCapacityLimiter(lease_ttl_seconds=int(settings.SSE_LEASE_TTL_SECONDS)),
)

__all__ = [
    "QUEUE_CAPACITY_UNAVAILABLE",
    "QUEUE_MAXSIZE",
    "QUEUE_MAX_BYTES",
    "QUEUE_OVERFLOW",
    "RunStreamBroker",
    "StreamCapacityUnavailableError",
    "StreamLimitExceededError",
    "StreamSubscription",
    "run_stream_broker",
]
