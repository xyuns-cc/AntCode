"""调度事件循环

从 Redis Streams 消费调度事件并驱动 Master 调度器更新。
"""

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
from antcode_core.common.utils.serialization import to_json
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from antcode_core.infrastructure.redis.streams import StreamClient
from loguru import logger


# P1-19: 死信队列 stream key。达阈值的事件先写这里再 ACK；写失败不 ACK。
def _dead_letter_stream_key(namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:dead_letter:scheduler_event"


# DLQ 保留上限（近似），防止死信本身无限膨胀。
_DLQ_MAXLEN = 10_000
_OUTBOX_HEARTBEAT_SECONDS = OUTBOX_CONSUME_CLAIM_TIMEOUT_SECONDS / 3


class OutboxClaimBusy(RuntimeError):
    """另一个 Master 正在处理同一 outbox_id；消息必须保留 PEL。"""


class SchedulerEventLoop:
    """调度事件循环"""

    # R1-P0-3 (审查报告)：失败事件不 ACK 永不重读，PEL 无限膨胀；且原
    # consumer 名带 os.getpid() 重启即变，旧 PEL 永远读不回。
    # - 死信阈值 = 一条消息最多被投递多少次仍处理失败，之后 ACK 进死信
    # - pending_check 周期 = 主循环空闲时读自己 PEL 兜底重试
    # - autoclaim min_idle = 认领其他 consumer 悬挂消息的阈值
    DLQ_DELIVERY_THRESHOLD = 5
    PENDING_CHECK_INTERVAL = 30.0
    AUTOCLAIM_MIN_IDLE_MS = 60_000

    def __init__(
        self,
        # P2-06: block_ms 从 3000 下调到 1000 —— XREADGROUP 处于 block 时
        # asyncio.CancelledError 需等 Redis 端 block 到期才返回，多个 loop
        # gather 停机时 block 越长 shutdown 拖延越严重。1s 已足够摊薄空转
        # 频次；关停时最坏多等 1s。
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
        # R1-P0-3: 固定 consumer 名（hostname+pid 已能区分多副本；重启后 pid
        # 会变，靠 XAUTOCLAIM 认领旧 PEL）
        self._consumer = f"{socket.gethostname()}-{os.getpid()}"
        self._stream_client = StreamClient()
        # T6-T2: delivery counter 走 XPENDING 权威源；_deliveries 已废弃。
        self._deliveries: dict[str, int] = {}
        self._last_pending_check = 0.0

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
        """事件循环主逻辑。

        R1-P0-3: 补上 pending 重读 + XAUTOCLAIM + 失败重试计数进死信，
        堵住"master crash 或事件处理抛错后批次事件永久丢失"这条链。
        """
        import time as _time

        while self._running:
            try:
                # T6-T2: 去掉 leader gate —— scheduler_event 也走 consumer
                # group（虽然生产者只有 leader，消费端可以多实例分担）。
                # PEL deliver_count 走 XPENDING 权威源（下方 _handle_message
                # 里读），不再依赖进程内 _deliveries 本地计数。

                await self._stream_client.ensure_group(self._stream, self._group)

                messages = await self._stream_client.xreadgroup(
                    stream_key=self._stream,
                    group_name=self._group,
                    consumer_name=self._consumer,
                    count=self.batch_size,
                    block_ms=self.block_ms,
                )

                # 无新消息：读 pending + XAUTOCLAIM 认领旧 consumer PEL
                if not messages:
                    now = _time.time()
                    if now - self._last_pending_check >= self.PENDING_CHECK_INTERVAL:
                        self._last_pending_check = now
                        try:
                            messages = await self._stream_client.xreadgroup(
                                stream_key=self._stream,
                                group_name=self._group,
                                consumer_name=self._consumer,
                                count=self.batch_size,
                                block_ms=1,
                                read_pending=True,
                            )
                        except TypeError:
                            # 兼容旧签名（不支持 read_pending 参数）
                            messages = []
                        if not messages:
                            try:
                                next_id = "0-0"
                                claimed_total = 0
                                for _ in range(3):
                                    next_id, claimed, _ = await self._stream_client.xautoclaim(
                                        self._stream,
                                        group_name=self._group,
                                        consumer_name=self._consumer,
                                        min_idle_time_ms=self.AUTOCLAIM_MIN_IDLE_MS,
                                        start_id=next_id,
                                        count=self.batch_size,
                                    )
                                    claimed_total += len(claimed)
                                    if not next_id or next_id == "0-0" or len(claimed) < self.batch_size:
                                        break
                                if claimed_total:
                                    logger.warning(
                                        "scheduler_event_loop XAUTOCLAIM 认领旧 PEL {} 条",
                                        claimed_total,
                                    )
                            except Exception as exc:
                                logger.debug(f"XAUTOCLAIM 失败: {exc}")
                    if not messages:
                        await asyncio.sleep(self.idle_sleep)
                        continue

                ack_ids: list[str] = []
                for message in messages:
                    try:
                        await self._handle_message(message.data)
                        ack_ids.append(message.msg_id)
                    except OutboxClaimBusy:
                        logger.debug(
                            "outbox claim 正由其他 Master 持有，保留 PEL: msg_id={}",
                            message.msg_id,
                        )
                    except Exception as e:
                        # R1-P0-3: 失败不 ACK 会永远卡在 PEL；累计投递次数
                        # 超阈值后 ACK 进死信，避免 PEL 无限膨胀。
                        # T6-T2: 计数改用 XPENDING deliver_count（Redis 权威源），
                        # 不再走进程内 _deliveries dict —— 多 master 分片后本地
                        # 计数会漂，同一条消息被两个实例累加导致误进死信。
                        try:
                            count = await self._xpending_deliver_count(message.msg_id)
                        except Exception as pend_exc:
                            logger.debug(f"XPENDING 查询失败，退回 1: {pend_exc}")
                            count = 1
                        if count >= self.DLQ_DELIVERY_THRESHOLD:
                            if await self._dead_letter_failed_message(message, e, count):
                                ack_ids.append(message.msg_id)
                        else:
                            logger.warning(f"处理调度事件失败 (第 {count} 次): msg_id={message.msg_id} err={e}")

                if ack_ids:
                    await self._stream_client.xack(self._stream, ack_ids, self._group)
                    try:
                        client = await self._stream_client._get_client()
                        await trim_acknowledged_stream(
                            client,
                            self._stream,
                            self._group,
                        )
                    except Exception:
                        logger.exception("调度事件 Stream ACK 后裁剪失败")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度事件循环异常: {e}")
                await asyncio.sleep(self.idle_sleep)

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
            await scheduler_outbox_service.heartbeat_consumption(outbox_id, self._consumer)

    async def _dispatch_message(self, data: dict) -> None:
        """按事件族路由业务处理；异常必须向上暴露给 PEL 重试。"""

        event_type = str(data.get("event", ""))

        if await self._dispatch_special_event(data, event_type):
            return

        task_id_raw = data.get("task_id")
        if not task_id_raw:
            logger.warning(f"调度事件缺少 task_id 且非 batch_* 事件: {event_type}")
            return

        try:
            task_id = int(task_id_raw)
        except (TypeError, ValueError):
            logger.warning(f"调度事件 task_id 无效: {task_id_raw}")
            return

        from antcode_core.domain.models.task import Task

        from antcode_master.control.scheduler_loop import scheduler_service

        if event_type == "task_trigger":
            # P1-DB-01: at-least-once 消费；outbox_id 作幂等键折叠重放。
            await scheduler_service.trigger_task(task_id, idempotency_key=str(data.get("outbox_id") or "") or None)
            return

        if event_type != "task_changed":
            logger.warning(f"未知调度事件类型: {event_type}")
            return

        task = await Task.get_or_none(id=task_id)
        if not task or not task.is_active:
            await scheduler_service.remove_task(task_id)
            return

        await scheduler_service.add_task(task)

    async def _dispatch_special_event(self, data: dict, event_type: str) -> bool:
        if event_type == "spider_storage_cleanup":
            await self._cleanup_spider_storage(data)
            return True
        if event_type == "task_logs_purge":
            await self._purge_task_logs(data)
            return True
        if not event_type.startswith("batch_"):
            return False
        batch_id = data.get("batch_id")
        if not batch_id:
            raise ValueError(f"批次事件缺 batch_id: {event_type}")
        from antcode_core.application.services.crawl.batch_dispatcher_service import (
            crawl_batch_dispatcher_service,
        )

        await crawl_batch_dispatcher_service.handle_batch_event(event_type, str(batch_id))
        return True

    @staticmethod
    async def _cleanup_spider_storage(data: dict) -> None:
        from antcode_core.application.services.crawl.spider_storage_cleanup import (
            SpiderStorageCleanupService,
        )
        from antcode_core.infrastructure.redis.keys import RedisKeys

        run_ids = data.get("run_ids")
        project_id = str(data.get("project_id") or "")
        if not isinstance(run_ids, list) or not all(isinstance(run_id, str) for run_id in run_ids):
            raise ValueError("spider_storage_cleanup run_ids 格式非法")
        redis = await get_redis_client()
        cleaner = SpiderStorageCleanupService(redis, RedisKeys(namespace=redis_namespace()))
        await cleaner.delete_runs(run_ids, project_id)

    @staticmethod
    async def _purge_task_logs(data: dict) -> None:
        """P1-round6 5.2 durable cleanup: delete_project_cascade 事务后同步
        purge_task_logs_for_runs 若崩溃, logs 变孤儿。改由 outbox 事件驱动:
        事务内 enqueue task_logs_purge, 消费失败自动 requeue, 达上限进 DLQ。
        purge_task_logs_for_runs 走 advisory lock 与 late writer 串行化, 天然
        幂等 (在锁内校验 TaskRun 已删除, 重复消费只是重复 DELETE 无匹配行)。
        """
        from antcode_core.application.services.logs.task_log_run_guard import (
            purge_task_logs_for_runs,
        )

        run_ids = data.get("run_ids")
        if not isinstance(run_ids, list) or not all(isinstance(rid, str) for rid in run_ids):
            raise ValueError("task_logs_purge run_ids 格式非法")
        if not run_ids:
            return
        deleted = await purge_task_logs_for_runs(run_ids)
        logger.info("outbox 驱动 task_logs_purge 完成: run_batch={} deleted={}", len(run_ids), deleted)


scheduler_event_loop = SchedulerEventLoop()
