"""Exact-message visibility recovery for explicitly deferred Direct tasks."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from antcode_worker.transport.redis.reclaim_models import text

DEFER_VISIBILITY_SECONDS = 30.0
DEFER_RETRY_SECONDS = 5.0

_CLAIM_DEFERRED_MESSAGE_LUA = """
if #ARGV ~= 3 then
    return redis.error_reply('invalid deferred claim arguments')
end
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 then
    return {-1, {}}
end
if pending[1][2] ~= ARGV[3] then
    return {-2, {}}
end
local delivery_count = pending[1][4]
local messages = redis.call(
    'XCLAIM', KEYS[1], ARGV[1], ARGV[3], 0, ARGV[2], 'RETRYCOUNT', delivery_count
)
if #messages ~= 1 then
    return redis.error_reply('deferred XCLAIM failed')
end
return {1, messages[1]}
"""


@dataclass(frozen=True)
class DeferredDelivery:
    stream_key: str
    message_id: str
    consumer_name: str
    visible_at: float

    @property
    def identity(self) -> str:
        return f"{self.stream_key}|{self.message_id}"


class DeferredTaskRecovery:
    def __init__(
        self,
        *,
        redis_provider: Callable[[], Any],
        consumer_group: str,
        current_consumer_name: Callable[[], str],
        generation_guard: Callable[[], Awaitable[bool]],
        on_visible: Callable[[str, dict[str, str]], Awaitable[None]],
        visibility_seconds: float = DEFER_VISIBILITY_SECONDS,
        retry_seconds: float = DEFER_RETRY_SECONDS,
    ) -> None:
        _validate_delay("visibility_seconds", visibility_seconds, allow_zero=True)
        _validate_delay("retry_seconds", retry_seconds, allow_zero=False)
        self._redis_provider = redis_provider
        self._consumer_group = consumer_group
        self._current_consumer_name = current_consumer_name
        self._generation_guard = generation_guard
        self._on_visible = on_visible
        self._visibility_seconds = visibility_seconds
        self._retry_seconds = retry_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def defer(
        self,
        *,
        stream_key: str,
        message_id: str,
        consumer_name: str,
    ) -> None:
        if not self._running:
            raise RuntimeError("deferred task recovery 尚未启动")
        delivery = DeferredDelivery(
            stream_key=stream_key,
            message_id=message_id,
            consumer_name=consumer_name,
            visible_at=asyncio.get_running_loop().time() + self._visibility_seconds,
        )
        if delivery.identity in self._tasks:
            raise RuntimeError("同一 task receipt 已登记 deferred recovery")
        self._tasks[delivery.identity] = asyncio.create_task(self._recover(delivery))

    async def _recover(self, delivery: DeferredDelivery) -> None:
        try:
            remaining = delivery.visible_at - asyncio.get_running_loop().time()
            await asyncio.sleep(max(0.0, remaining))
            while self._running:
                generation_state = await self._generation_state(delivery)
                if generation_state is False:
                    return
                if generation_state is None:
                    await asyncio.sleep(self._retry_seconds)
                    continue
                if await self._try_deliver(delivery):
                    return
                await asyncio.sleep(self._retry_seconds)
        except asyncio.CancelledError:
            raise
        finally:
            current = asyncio.current_task()
            if self._tasks.get(delivery.identity) is current:
                self._tasks.pop(delivery.identity, None)

    async def _try_deliver(self, delivery: DeferredDelivery) -> bool:
        try:
            redis = self._redis_provider()
            if redis is None:
                raise RuntimeError("deferred recovery Redis 尚未连接")
            result = await redis.eval(
                _CLAIM_DEFERRED_MESSAGE_LUA,
                1,
                delivery.stream_key,
                self._consumer_group,
                delivery.message_id,
                delivery.consumer_name,
            )
            status, message = _decode_claim_result(result)
            if status < 0:
                logger.info(
                    "deferred task 已不在当前 consumer PEL: message_id={} status={}",
                    delivery.message_id,
                    status,
                )
                return True
            if not await self._owns_generation(delivery):
                return True
            await self._on_visible(message[0], message[1])
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("deferred task 精确恢复失败: message_id={}", delivery.message_id)
            return False

    async def _owns_generation(self, delivery: DeferredDelivery) -> bool:
        if delivery.consumer_name != self._current_consumer_name():
            return False
        return bool(await self._generation_guard())

    async def _generation_state(self, delivery: DeferredDelivery) -> bool | None:
        try:
            return await self._owns_generation(delivery)
        except Exception:
            logger.exception("deferred task generation 校验失败: message_id={}", delivery.message_id)
            return None


def _decode_claim_result(result: Any) -> tuple[int, tuple[str, dict[str, str]]]:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise RuntimeError("deferred XCLAIM 返回值无效")
    status = int(result[0])
    if status not in (-2, -1, 1):
        raise RuntimeError("deferred XCLAIM 状态无效")
    if status < 0:
        return status, ("", {})
    return status, _decode_message(result[1])


def _decode_message(raw: Any) -> tuple[str, dict[str, str]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise RuntimeError("deferred XCLAIM message 无效")
    return text(raw[0]), _decode_fields(raw[1])


def _decode_fields(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {text(key): text(value) for key, value in raw.items()}
    if not isinstance(raw, (list, tuple)) or len(raw) % 2 != 0:
        raise RuntimeError("deferred XCLAIM fields 无效")
    return {text(raw[index]): text(raw[index + 1]) for index in range(0, len(raw), 2)}


def _validate_delay(name: str, value: Any, *, allow_zero: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"deferred {name} 必须是有限数值")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"deferred {name} 超出有效范围")
