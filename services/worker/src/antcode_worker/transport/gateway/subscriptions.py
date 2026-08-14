"""Gateway server-stream subscription lifecycle and health reporting."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from antcode_worker.transport.gateway.status_error_policy import is_lease_fence_error

TASK_SUBSCRIPTION = "StreamTasks"
CONTROL_SUBSCRIPTION = "WatchControl"
SUBSCRIPTION_NAMES = (TASK_SUBSCRIPTION, CONTROL_SUBSCRIPTION)


@dataclass(frozen=True)
class SubscriptionRetryConfig:
    initial_backoff: float
    max_backoff: float
    backoff_multiplier: float


@dataclass(frozen=True)
class SubscriptionHooks:
    is_running: Callable[[], bool]
    consume: Callable[[Callable[[], Awaitable[None]]], Awaitable[bool]]
    set_health: Callable[[str, bool], Awaitable[None]]
    revoke_lease: Callable[[], Awaitable[None]]


class SubscriptionRunner:
    """Run one reconnecting server stream and report its exact health."""

    def __init__(self, config: SubscriptionRetryConfig) -> None:
        self._config = config

    async def run(self, name: str, hooks: SubscriptionHooks) -> None:
        backoff = self._config.initial_backoff

        async def mark_open() -> None:
            await hooks.set_health(name, True)

        while hooks.is_running():
            try:
                received = await hooks.consume(mark_open)
            except asyncio.CancelledError:
                if hooks.is_running():
                    await hooks.set_health(name, False)
                raise
            except Exception as exc:
                if not hooks.is_running():
                    return
                await hooks.set_health(name, False)
                if is_lease_fence_error(exc):
                    await hooks.revoke_lease()
                    return
                backoff = await self._wait_retry(name, backoff, exc)
                continue
            await hooks.set_health(name, False)
            if received:
                backoff = self._config.initial_backoff
            if not received and hooks.is_running():
                await asyncio.sleep(self._config.initial_backoff)

    async def _wait_retry(self, name: str, backoff: float, error: Exception) -> float:
        logger.warning("{} 流结束: {}，{:.1f}s 后重连", name, error, backoff)
        await asyncio.sleep(backoff)
        return min(
            self._config.max_backoff,
            backoff * self._config.backoff_multiplier,
        )
