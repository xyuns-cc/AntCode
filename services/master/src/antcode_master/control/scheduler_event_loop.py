"""从 Redis Streams 消费调度事件并驱动 Master 调度器更新。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time

from antcode_core.application.services.scheduler.outbox_service import (
    OUTBOX_CONSUME_CLAIM_TIMEOUT_SECONDS,
    OutboxConsumeClaim,
    scheduler_outbox_service,
)
from antcode_core.common.config import settings
from antcode_core.common.serialization import to_json
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.stream_client import StreamClient, StreamMessage
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from loguru import logger

from antcode_master.control.scheduler_authority import SchedulerAuthorityLost
from antcode_master.control.scheduler_event_handlers import dispatch_special_event
from antcode_master.control.scheduler_task_events import dispatch_task_event
from antcode_master.control.trigger_idempotency import TriggerDeferred
from antcode_master.leader import ensure_leader


# P1-19: 死信队列 stream key。达阈值的事件先写这里再 ACK；写失败不 ACK。
def _dead_letter_stream_key(namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:dead_letter:scheduler_event"


# DLQ 保留上限（近似），防止死信本身无限膨胀。
_DLQ_MAXLEN = 10_000
_OUTBOX_HEARTBEAT_SECONDS = OUTBOX_CONSUME_CLAIM_TIMEOUT_SECONDS / 3


class OutboxClaimBusy(RuntimeError):
    """另一个 Master 正在处理同一 outbox_id；消息必须保留 PEL。"""


class LeaderAuthorityLost(RuntimeError):
    """当前 Master 已失去 Redis 权威 Leader 身份。"""


class SchedulerEventLoop:
    """调度事件循环"""

    # - 死信阈值 = 一条消息最多被投递多少次仍处理失败，之后 ACK 进死信
    # - autoclaim min_idle = 认领其他 consumer 悬挂消息的阈值
    DLQ_DELIVERY_THRESHOLD = 5
    PENDING_CHECK_INTERVAL = 30.0
    AUTOCLAIM_MIN_IDLE_MS = 60_000

    def __init__(
        self,
        # 1s Redis block 限制取消时的最坏停机延迟。
        block_ms: int = 1000,
        batch_size: int = 50,
        idle_sleep: float = 1.0,
    ):
        self.block_ms = block_ms
        self.batch_size = batch_size
        self.idle_sleep = idle_sleep
        self._running = False
        self._task: asyncio.Task | None = None
        self._stream = settings.scheduler_event_stream
        self._group = settings.SCHEDULER_EVENT_GROUP
        self._consumer = f"{socket.gethostname()}-{os.getpid()}"
        self._stream_client = StreamClient()
        self._last_pending_check = 0.0
        self._autoclaim_cursor = "0-0"

    async def start(self) -> None:
        """启动事件循环"""
        if self._running:
            logger.warning("调度事件循环已在运行")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"调度事件循环已启动: stream={self._stream}, group={self._group}, consumer={self._consumer}")

    async def stop(self) -> None:
        """停止事件循环"""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("调度事件循环已停止")

    async def _run_loop(self) -> None:
        """仅由 Redis 权威 Leader 消费调度事件。"""
        while self._running:
            try:
                await self._run_iteration()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度事件循环异常: {e}")
                await asyncio.sleep(self.idle_sleep)

    async def _run_iteration(self) -> None:
        if not await ensure_leader():
            await asyncio.sleep(self.idle_sleep)
            return
        await self._stream_client.ensure_group(self._stream, self._group)
        messages = await self._read_messages()
        if not messages:
            await asyncio.sleep(self.idle_sleep)
            return
        await self._process_messages(messages)

    async def _read_messages(self) -> list[StreamMessage]:
        if time.monotonic() - self._last_pending_check >= self.PENDING_CHECK_INTERVAL:
            messages = await self._recover_pending_messages()
            self._last_pending_check = time.monotonic()
            if messages:
                return messages
        return await self._stream_client.xreadgroup(
            stream_key=self._stream,
            group_name=self._group,
            consumer_name=self._consumer,
            count=self.batch_size,
            block_ms=self.block_ms,
        )

    async def _recover_pending_messages(self) -> list[StreamMessage]:
        own_pending = await self._stream_client.xreadgroup(
            stream_key=self._stream,
            group_name=self._group,
            consumer_name=self._consumer,
            count=self.batch_size,
            block_ms=1,
            read_pending=True,
        )
        next_id, claimed, _deleted = await self._stream_client.xautoclaim(
            self._stream,
            group_name=self._group,
            consumer_name=self._consumer,
            min_idle_time_ms=self.AUTOCLAIM_MIN_IDLE_MS,
            start_id=self._autoclaim_cursor,
            count=self.batch_size,
        )
        self._autoclaim_cursor = next_id or "0-0"
        if claimed:
            logger.warning("scheduler_event_loop XAUTOCLAIM 认领旧 PEL {} 条", len(claimed))
        by_id = {message.msg_id: message for message in own_pending}
        by_id.update({message.msg_id: message for message in claimed})
        return list(by_id.values())

    async def _process_messages(self, messages: list[StreamMessage]) -> None:
        ack_ids: list[str] = []
        for message in messages:
            if not await ensure_leader():
                return
            should_ack = await self._process_message(message)
            if not await ensure_leader():
                return
            if should_ack:
                ack_ids.append(message.msg_id)
        if not ack_ids or not await ensure_leader():
            return
        await self._ack_messages(ack_ids)

    async def _process_message(self, message: StreamMessage) -> bool:
        try:
            await self._handle_message(message.data)
            return True
        except OutboxClaimBusy:
            logger.debug(
                "outbox claim 正由其他 Master 持有，保留 PEL: msg_id={}",
                message.msg_id,
            )
            return False
        except TriggerDeferred as exc:
            logger.info("task_trigger 暂不可接纳，保留 PEL: msg_id={} err={}", message.msg_id, exc)
            return False
        except (LeaderAuthorityLost, SchedulerAuthorityLost):
            logger.warning("处理期间失去调度 authority，保留 PEL: msg_id={}", message.msg_id)
            return False
        except Exception as exc:
            return await self._handle_processing_failure(message, exc)

    async def _handle_processing_failure(self, message: StreamMessage, error: Exception) -> bool:
        try:
            count = await self._xpending_deliver_count(message.msg_id)
        except Exception as exc:
            logger.warning("XPENDING 查询失败，保留 PEL: msg_id={} err={}", message.msg_id, exc)
            return False
        if count >= self.DLQ_DELIVERY_THRESHOLD:
            return await self._dead_letter_failed_message(message, error, count)
        logger.warning("处理调度事件失败 (第 {} 次): msg_id={} err={}", count, message.msg_id, error)
        return False

    async def _ack_messages(self, ack_ids: list[str]) -> None:
        await self._stream_client.xack(self._stream, ack_ids, self._group)
        try:
            client = await self._stream_client._get_client()
            await trim_acknowledged_stream(client, self._stream, self._group)
        except Exception:
            logger.exception("调度事件 Stream ACK 后裁剪失败")

    async def _write_to_dlq(
        self,
        *,
        stream_key: str,
        msg_id: str,
        payload: dict,
        reason: str,
    ) -> bool:
        """P1-19: 把达阈值的调度事件搬到 DLQ；写入成功返回 True。

        DLQ 内保留原 payload（JSON 序列化）+ 追溯字段 orig_stream / orig_msg_id
        / reason / moved_at_ms，便于人工排障。写入失败返回 False，让调用方
        选择保留 PEL 而不是 ACK 丢消息。
        """
        try:
            redis = await get_redis_client()
            # payload 内部可能有 dict / list / int / str，统一 JSON 化以确保
            # Redis Streams 字段能接受（Streams 只接受 str/bytes/int/float）。
            entry: dict[str | bytes | int | float, bytes | str | int | float] = {
                "orig_stream": stream_key,
                "orig_msg_id": msg_id,
                "reason": reason,
                "moved_at_ms": str(int(time.time() * 1000)),
                "payload": to_json(payload),
            }
            await redis.xadd(
                _dead_letter_stream_key(),
                entry,
                maxlen=_DLQ_MAXLEN,
                approximate=True,
            )
            return True
        except Exception as exc:
            logger.exception(
                "写调度事件 DLQ 失败(将保留 PEL 由下轮重试): msg_id={} err={}",
                msg_id,
                exc,
            )
            return False

    async def _xpending_deliver_count(self, msg_id: str) -> int:
        """T6-T2: 查 XPENDING 里指定 msg 的投递次数。

        Redis Streams 每次 XREADGROUP / XCLAIM 触达一条 PEL 消息都会自增
        times_delivered；这是跨 master 实例的权威计数。
        """
        client = await self._stream_client._get_client()  # type: ignore[attr-defined]
        raw = await client.xpending_range(self._stream, self._group, min=msg_id, max=msg_id, count=1)
        if not raw:
            return 1
        entry = raw[0]
        # redis-py 返回 dict 或 list（版本差异），都提取 times_delivered
        if isinstance(entry, dict):
            return int(entry.get("times_delivered", 1))
        # 兼容 list 形式 [id, consumer, idle, delivered]
        try:
            return int(entry[3])
        except (IndexError, TypeError, ValueError):
            return 1

    async def _dead_letter_failed_message(self, message, error: Exception, count: int) -> bool:
        reason = f"delivery_count>={count}: {error}"
        if not await self._write_to_dlq(
            stream_key=self._stream,
            msg_id=message.msg_id,
            payload=message.data,
            reason=reason,
        ):
            logger.error("调度事件 DLQ 写入失败，保留 PEL: msg_id={}", message.msg_id)
            return False
        outbox_id = str(message.data.get("outbox_id") or "")
        if outbox_id:
            try:
                requeued = await scheduler_outbox_service.requeue_consumption_failure(outbox_id, reason)
            except Exception:
                logger.exception("调度 outbox 耐久重投标记失败，保留 PEL: outbox_id={}", outbox_id)
                return False
            # L3: 达消费重投上限后 requeue_consumption_failure 返回 False —— 事件已
            # 被终止(标记 consumed),不再 republish。DLQ 记录仍在,ACK 掉 Redis
            # 消息即可,避免 poison 事件无限循环。
            if not requeued:
                logger.error(
                    "调度 outbox 消费重投达上限,终止不再重投(已入 DLQ): outbox_id={}",
                    outbox_id,
                )
        logger.error(
            "调度事件失败 {} 次，已入 DLQ{}: msg_id={} event={} err={}",
            count,
            "并耐久重投" if outbox_id else "",
            message.msg_id,
            message.data,
            error,
        )
        return True

    async def _handle_message(self, data: dict) -> None:
        """通过 PostgreSQL durable claim 保证同一 outbox_id 单消费者执行。"""
        outbox_id = str(data.get("outbox_id") or "")
        if not outbox_id:
            await self._dispatch_message(data)
            return

        claim = await scheduler_outbox_service.claim_consumption(outbox_id, self._consumer)
        if claim == OutboxConsumeClaim.CONSUMED:
            logger.debug("outbox 事件已完成，重复消息可 ACK: outbox_id={}", outbox_id)
            return
        if claim == OutboxConsumeClaim.BUSY:
            raise OutboxClaimBusy(outbox_id)

        try:
            await self._dispatch_with_claim_heartbeat(data, outbox_id)
            if not await ensure_leader():
                raise LeaderAuthorityLost(outbox_id)
            await scheduler_outbox_service.complete_consumption(outbox_id, self._consumer)
        except BaseException:
            await scheduler_outbox_service.release_consumption(outbox_id, self._consumer)
            raise

    async def _dispatch_with_claim_heartbeat(self, data: dict, outbox_id: str) -> None:
        business = asyncio.create_task(self._dispatch_message(data))
        heartbeat = asyncio.create_task(self._heartbeat_outbox_claim(outbox_id))
        try:
            done, _pending = await asyncio.wait(
                {business, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if business in done:
                await business
                return
            business.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await business
            await heartbeat
        finally:
            for task in (business, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(business, heartbeat, return_exceptions=True)

    async def _heartbeat_outbox_claim(self, outbox_id: str) -> None:
        while True:
            await asyncio.sleep(_OUTBOX_HEARTBEAT_SECONDS)
            if not await ensure_leader():
                raise LeaderAuthorityLost(outbox_id)
            await scheduler_outbox_service.heartbeat_consumption(outbox_id, self._consumer)

    async def _dispatch_message(self, data: dict) -> None:
        """按事件族路由业务处理；异常必须向上暴露给 PEL 重试。"""

        event_type = str(data.get("event", ""))

        if await dispatch_special_event(data, event_type):
            return
        if await dispatch_task_event(data, event_type):
            return
        logger.warning(f"未知调度事件类型: {event_type}")


scheduler_event_loop = SchedulerEventLoop()
