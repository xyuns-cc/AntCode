"""调度事件循环

从 Redis Streams 消费调度事件并驱动 Master 调度器更新。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from typing import Any

from antcode_core.common.config import settings
from antcode_core.common.utils.serialization import to_json
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.streams import StreamClient
from loguru import logger

from antcode_master.leader import ensure_leader


# P1-19: 死信队列 stream key。达阈值的事件先写这里再 ACK；写失败不 ACK。
def _dead_letter_stream_key(namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:dead_letter:scheduler_event"


# DLQ 保留上限（近似），防止死信本身无限膨胀。
_DLQ_MAXLEN = 10_000


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
        block_ms: int = 3000,
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
        # T6-T2: delivery counter 走 XPENDING 权威源，不再用进程内 dict。
        # _deliveries 已废弃，保留字段名以防外部访问但不用于决策。
        self._deliveries: dict[str, int] = {}
        self._last_pending_check = 0.0

    async def start(self) -> None:
        """启动事件循环"""
        if self._running:
            logger.warning("调度事件循环已在运行")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"调度事件循环已启动: stream={self._stream}, group={self._group}, "
            f"consumer={self._consumer}"
        )

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
                if not settings.REDIS_ENABLED:
                    await asyncio.sleep(self.idle_sleep)
                    continue

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
                            # P1-19: 到阈值不能直接 ACK，否则事件正文丢失。
                            # 先写 DLQ、写成功再 ACK；DLQ 写失败则 keep-in-PEL
                            # 交给下一轮 XAUTOCLAIM 再试，不静默丢消息。
                            dlq_ok = await self._write_to_dlq(
                                stream_key=self._stream,
                                msg_id=message.msg_id,
                                payload=message.data,
                                reason=f"delivery_count>={count}: {e}",
                            )
                            if dlq_ok:
                                logger.error(
                                    "调度事件失败 {} 次 → 已入 DLQ + ACK: "
                                    "msg_id={} event={} err={}",
                                    count, message.msg_id, message.data, e,
                                )
                                ack_ids.append(message.msg_id)
                            else:
                                # DLQ 写入失败——保留 PEL，下轮 XAUTOCLAIM
                                # 会再次触达；同时告警便于人工介入。
                                logger.error(
                                    "调度事件失败 {} 次 但 DLQ 写入失败，保留 PEL: "
                                    "msg_id={} event={}",
                                    count, message.msg_id, message.data,
                                )
                        else:
                            logger.warning(
                                f"处理调度事件失败 (第 {count} 次): msg_id={message.msg_id} err={e}"
                            )

                if ack_ids:
                    await self._stream_client.xack(self._stream, ack_ids, self._group)

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
            entry: dict[str, Any] = {
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
                "写调度事件 DLQ 失败(将保留 PEL 由下轮重试): "
                "msg_id={} err={}",
                msg_id, exc,
            )
            return False

    async def _xpending_deliver_count(self, msg_id: str) -> int:
        """T6-T2: 查 XPENDING 里指定 msg 的投递次数。

        Redis Streams 每次 XREADGROUP / XCLAIM 触达一条 PEL 消息都会自增
        times_delivered；这是跨 master 实例的权威计数。
        """
        client = await self._stream_client._get_client()  # type: ignore[attr-defined]
        raw = await client.xpending_range(
            self._stream, self._group, min=msg_id, max=msg_id, count=1
        )
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

    async def _handle_message(self, data: dict) -> None:
        """处理单条事件。

        F: 分两族事件：
        - **task_*** 事件：依赖 ``task_id``，走 scheduler_service（原逻辑）。
        - **batch_*** 事件：依赖 ``batch_id``，走 crawl_batch_dispatcher_service。
          旧实现遇到 batch 事件因 ``task_id`` 缺失直接丢，是 crawl 主链路的
          断点之一。
        """
        event_type = str(data.get("event", ""))

        # F: batch_* 事件分支
        if event_type.startswith("batch_"):
            batch_id = data.get("batch_id")
            if not batch_id:
                logger.warning(f"批次事件缺 batch_id: {event_type}")
                return
            from antcode_core.application.services.crawl.batch_dispatcher_service import (
                crawl_batch_dispatcher_service,
            )

            await crawl_batch_dispatcher_service.handle_batch_event(
                event_type, str(batch_id)
            )
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
            await scheduler_service.trigger_task(task_id)
            return

        if event_type != "task_changed":
            logger.warning(f"未知调度事件类型: {event_type}")
            return

        task = await Task.get_or_none(id=task_id)
        if not task or not task.is_active:
            await scheduler_service.remove_task(task_id)
            return

        await scheduler_service.add_task(task)


scheduler_event_loop = SchedulerEventLoop()

