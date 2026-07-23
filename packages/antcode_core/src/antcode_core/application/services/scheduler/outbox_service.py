"""PostgreSQL transactional outbox and recovery publisher."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.application.services.scheduler.outbox_claims import OutboxConsumeClaim, claim_is_active
from antcode_core.common.config import settings
from antcode_core.domain.models.scheduler_outbox import SchedulerOutbox
from antcode_core.infrastructure.redis.streams import StreamClient

OUTBOX_POLL_SECONDS = 1.0
OUTBOX_BATCH_SIZE = 50
OUTBOX_MAX_BACKOFF_SECONDS = 300
OUTBOX_CONSUME_CLAIM_TIMEOUT_SECONDS = 60
OUTBOX_CONSUME_REQUEUE_SECONDS = 30
# L3: 消费侧重投次数上限。达到后终止(标记 consumed),不再让事件重新可发布,
# 防止永久失败的 outbox 事件每 ~30s 无限 republish→重复副作用→再写一份 DLQ。
OUTBOX_CONSUME_MAX_ATTEMPTS = 5

# P1-round6 5.2: 终止性标记前缀, 区分"真正业务消费"与"重试耗尽放弃"。
# 达上限时 consumed_at 仍写(防重投), last_error 前缀让运维和统计可识别。
# 查询终止事件: SchedulerOutbox.filter(last_error__startswith=OUTBOX_TERMINATED_PREFIX)
OUTBOX_TERMINATED_PREFIX = "[TERMINATED_MAX_RETRIES] "


class SchedulerOutboxService:
    """Commit scheduler events in PostgreSQL and publish them at least once."""

    def __init__(self, stream: StreamClient | None = None):
        self._stream = stream or StreamClient()
        self._running = False
        self._task: asyncio.Task | None = None

    async def enqueue(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str | int,
        payload: dict[str, Any],
        connection: Any | None = None,
    ) -> SchedulerOutbox:
        available_at = datetime.now(UTC)
        if connection is None:
            return await SchedulerOutbox.create(
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=str(aggregate_id),
                payload=dict(payload),
                available_at=available_at,
            )
        return await SchedulerOutbox.create(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            payload=dict(payload),
            available_at=available_at,
            using_db=connection,
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("scheduler outbox publisher 已启动")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    @staticmethod
    async def _db_now(conn: Any) -> datetime:
        """P1-DB-03: claim/heartbeat 的时间线必须以数据库时钟为唯一权威。

        各 Master 主机 ``datetime.now()`` 存在偏差时，快钟节点会把慢钟
        节点仍在心跳的 claim 误判为 stale 并发接管，双消费者并发执行。
        """
        _, rows = await conn.execute_query("SELECT NOW() AS now")
        value = rows[0].get("now") if rows else None
        if not isinstance(value, datetime):
            raise RuntimeError("无法读取数据库时钟")
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    async def claim_consumption(self, outbox_id: str, owner: str) -> OutboxConsumeClaim:
        """原子 claim 一个 outbox 事件；陈旧 claim 可被新 owner 接管。

        注意：消费语义是 at-least-once —— 业务副作用执行后、
        ``complete_consumption`` 提交前进程崩溃，claim 超时后会被重新
        接管再执行一次。事件处理器必须自身幂等（run_id/outbox_id 级
        去重），这是 outbox 模式的硬性契约。
        """
        async with in_transaction("default") as conn:
            now = await self._db_now(conn)
            stale_before = now - timedelta(seconds=OUTBOX_CONSUME_CLAIM_TIMEOUT_SECONDS)
            event = await SchedulerOutbox.filter(public_id=outbox_id).using_db(conn).select_for_update().first()
            if event is None:
                raise LookupError(f"scheduler outbox 不存在: outbox_id={outbox_id}")
            if event.consumed_at is not None:
                return OutboxConsumeClaim.CONSUMED
            if claim_is_active(event, stale_before):
                return OutboxConsumeClaim.BUSY
            updated = (
                await SchedulerOutbox.filter(id=event.id)
                .using_db(conn)
                .update(
                    consume_owner=owner,
                    consume_started_at=now,
                )
            )
            if updated != 1:
                raise RuntimeError(f"scheduler outbox claim 失败: outbox_id={outbox_id}")
        return OutboxConsumeClaim.CLAIMED

    async def heartbeat_consumption(self, outbox_id: str, owner: str) -> None:
        async with in_transaction("default") as conn:
            now = await self._db_now(conn)
            updated = (
                await SchedulerOutbox.filter(
                    public_id=outbox_id,
                    consume_owner=owner,
                    consumed_at__isnull=True,
                )
                .using_db(conn)
                .update(consume_started_at=now)
            )
        if updated != 1:
            raise RuntimeError(f"scheduler outbox claim 已丢失: outbox_id={outbox_id}")

    async def complete_consumption(self, outbox_id: str, owner: str) -> None:
        updated = await SchedulerOutbox.filter(
            public_id=outbox_id,
            consume_owner=owner,
            consumed_at__isnull=True,
        ).update(
            consumed_at=datetime.now(UTC),
            consume_owner=None,
            consume_started_at=None,
        )
        if updated != 1:
            raise RuntimeError(f"scheduler outbox 完成标记失败: outbox_id={outbox_id}")

    async def release_consumption(self, outbox_id: str, owner: str) -> None:
        await SchedulerOutbox.filter(
            public_id=outbox_id,
            consume_owner=owner,
            consumed_at__isnull=True,
        ).update(
            consume_owner=None,
            consume_started_at=None,
        )

    async def requeue_consumption_failure(self, outbox_id: str, reason: str) -> bool:
        """消费失败后的耐久处理:未达上限 → 事件重新可发布重投;达上限 →
        终止(标记 ``consumed_at``)不再重投,防止永久失败的 poison 事件每
        ~30s 无限 republish→重复副作用→再写一份 DLQ。DLQ 记录已由调用方写入,
        终止后仍可人工排查。

        返回 ``True`` 表示已重投(将再次消费),``False`` 表示已终止放弃。

        P2 §4.5: 消费重投计数使用独立的 ``consume_attempts`` 列，不再与
        publish 侧 ``attempts`` 共享 —— 发布阶段曾退避多次的事件不会在
        消费侧首次失败就被终止。

        复审 P1-DB-01: 另一消费者持有活跃 claim 时绝不改写该行——按"已由
        接管者处理"返回 True；接管者崩溃的兜底是 claim 超时 + PEL 重投。
        """
        async with in_transaction("default") as conn:
            now = await self._db_now(conn)
            event = (
                await SchedulerOutbox.filter(public_id=outbox_id, consumed_at__isnull=True)
                .using_db(conn)
                .select_for_update()
                .first()
            )
            if event is None:
                raise RuntimeError(f"scheduler outbox 消费失败重投标记失败: outbox_id={outbox_id}")
            stale_before = now - timedelta(seconds=OUTBOX_CONSUME_CLAIM_TIMEOUT_SECONDS)
            if claim_is_active(event, stale_before):
                logger.warning(
                    "outbox 消费失败重投被跳过(事件已被 {} 活跃接管): outbox_id={}",
                    event.consume_owner,
                    outbox_id,
                )
                return True
            consume_attempts = int(getattr(event, "consume_attempts", 0) or 0) + 1
            if consume_attempts >= OUTBOX_CONSUME_MAX_ATTEMPTS:
                # P1-round6 5.2: 达上限写 consumed_at 防重投, 但 last_error 加
                # OUTBOX_TERMINATED_PREFIX 让运维可识别"重试耗尽"与"业务成功"。
                marked_reason = f"{OUTBOX_TERMINATED_PREFIX}{reason}"[:2000]
                updated = (
                    await SchedulerOutbox.filter(id=event.id)
                    .using_db(conn)
                    .update(
                        consume_attempts=consume_attempts,
                        consumed_at=now,
                        consume_owner=None,
                        consume_started_at=None,
                        last_error=marked_reason,
                    )
                )
                terminal = True
            else:
                updated = (
                    await SchedulerOutbox.filter(id=event.id)
                    .using_db(conn)
                    .update(
                        consume_attempts=consume_attempts,
                        published_at=None,
                        available_at=now + timedelta(seconds=OUTBOX_CONSUME_REQUEUE_SECONDS),
                        consume_owner=None,
                        consume_started_at=None,
                        last_error=reason[:2000],
                    )
                )
                terminal = False
            if updated != 1:
                raise RuntimeError(f"scheduler outbox 消费失败重投标记失败: outbox_id={outbox_id}")
        return not terminal

    async def _run(self) -> None:
        while self._running:
            try:
                published = await self.publish_available()
                if published == 0:
                    await asyncio.sleep(OUTBOX_POLL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("scheduler outbox publisher 异常")
                await asyncio.sleep(OUTBOX_POLL_SECONDS)

    async def publish_available(self) -> int:
        published = 0
        for _ in range(OUTBOX_BATCH_SIZE):
            handled = await self._publish_one()
            if not handled:
                break
            published += 1
        return published

    async def _publish_one(self) -> bool:
        now = datetime.now(UTC)
        async with in_transaction("default") as conn:
            event = await (
                SchedulerOutbox.filter(
                    published_at__isnull=True,
                    consumed_at__isnull=True,
                    available_at__lte=now,
                )
                .using_db(conn)
                .select_for_update(skip_locked=True)
                .order_by("id")
                .first()
            )
            if event is None:
                return False
            try:
                await self._publish(event)
            except Exception as exc:
                await self._defer(event, exc, conn)
                return True
            await (
                SchedulerOutbox.filter(id=event.id)
                .using_db(conn)
                .update(
                    published_at=now,
                    last_error=None,
                )
            )
        return True

    async def _publish(self, event: SchedulerOutbox) -> None:
        fields = dict(event.payload or {})
        fields["event"] = event.event_type
        fields["outbox_id"] = event.public_id
        fields["timestamp"] = event.created_at.isoformat()
        await self._stream.xadd(settings.scheduler_event_stream, fields)

    async def _defer(self, event: SchedulerOutbox, exc: Exception, conn: Any) -> None:
        attempts = int(event.attempts or 0) + 1
        delay = min(2 ** min(attempts, 8), OUTBOX_MAX_BACKOFF_SECONDS)
        await (
            SchedulerOutbox.filter(id=event.id)
            .using_db(conn)
            .update(
                attempts=attempts,
                available_at=datetime.now(UTC) + timedelta(seconds=delay),
                last_error=str(exc)[:2000],
            )
        )
        logger.warning(
            "scheduler outbox 发布失败，已延期: id={} attempts={} error={}",
            event.public_id,
            attempts,
            exc,
        )


scheduler_outbox_service = SchedulerOutboxService()

__all__ = [
    "OutboxConsumeClaim",
    "SchedulerOutboxService",
    "scheduler_outbox_service",
]
