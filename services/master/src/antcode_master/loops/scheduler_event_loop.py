"""调度事件循环

从 Redis Streams 消费调度事件并驱动 Master 调度器更新。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.infrastructure.redis.streams import StreamClient
from antcode_master.leader import ensure_leader


class SchedulerEventLoop:
    """调度事件循环"""

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
        self._consumer = f"{socket.gethostname()}-{os.getpid()}"
        self._stream_client = StreamClient()

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
        """事件循环主逻辑"""
        while self._running:
            try:
                if not settings.REDIS_ENABLED:
                    await asyncio.sleep(self.idle_sleep)
                    continue

                if not await ensure_leader():
                    await asyncio.sleep(self.idle_sleep)
                    continue

                await self._stream_client.ensure_group(self._stream, self._group)

                messages = await self._stream_client.xreadgroup(
                    stream_key=self._stream,
                    group_name=self._group,
                    consumer_name=self._consumer,
                    count=self.batch_size,
                    block_ms=self.block_ms,
                )

                if not messages:
                    continue

                ack_ids: list[str] = []
                for message in messages:
                    try:
                        await self._handle_message(message.data)
                        ack_ids.append(message.msg_id)
                    except Exception as e:
                        logger.error(f"处理调度事件失败: {e}")

                if ack_ids:
                    await self._stream_client.xack(self._stream, ack_ids, self._group)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度事件循环异常: {e}")
                await asyncio.sleep(self.idle_sleep)

    async def _handle_message(self, data: dict) -> None:
        """处理单条事件"""
        event_type = str(data.get("event", ""))
        task_id_raw = data.get("task_id")

        if not task_id_raw:
            logger.warning("调度事件缺少 task_id，已忽略")
            return

        try:
            task_id = int(task_id_raw)
        except (TypeError, ValueError):
            logger.warning(f"调度事件 task_id 无效: {task_id_raw}")
            return

        from antcode_core.domain.models.task import Task
        from antcode_master.loops.scheduler_loop import scheduler_service

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

