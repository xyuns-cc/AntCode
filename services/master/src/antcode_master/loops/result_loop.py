"""
结果消费循环

从 Redis Streams 消费 Worker 上报的执行结果并更新 TaskRun。

P1a 改造：Stream 上的消息现在是 Proto bytes（``data_pb2.TaskStatus``），
通过 ``StreamClient(codec=ProtoCodec(...))`` 自动解码为 typed 对象。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from datetime import UTC, datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.task_run_service import task_run_service
from antcode_core.infrastructure.redis import task_result_stream
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from loguru import logger

from antcode_master.leader import ensure_leader


class ResultLoop:
    """结果消费循环"""

    def __init__(
        self,
        stream_key: str | None = None,
        group_name: str = "antcode-results",
        consumer_name: str | None = None,
        poll_interval: float = 1.0,
        block_ms: int = 5000,
        batch_size: int = 50,
        pending_check_interval: int = 30,
    ):
        self._stream_key = stream_key or task_result_stream()
        self._group = group_name
        self._consumer = consumer_name or f"{socket.gethostname()}-{id(self)}"
        self._poll_interval = poll_interval
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._pending_check_interval = pending_check_interval
        self._last_pending_check = 0.0
        # Stream 上现在是 Proto bytes（TaskStatus），用 ProtoCodec 解码
        self._stream = StreamClient(codec=ProtoCodec(data_pb2.TaskStatus))
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动结果循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "结果消费循环已启动: stream={}, group={}, consumer={}",
            self._stream_key,
            self._group,
            self._consumer,
        )

    async def stop(self) -> None:
        """停止结果循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("结果消费循环已停止")

    async def _run_loop(self) -> None:
        """主循环"""
        while self._running:
            try:
                if not await ensure_leader():
                    await asyncio.sleep(self._poll_interval)
                    continue

                messages = await self._stream.xreadgroup_typed(
                    stream_key=self._stream_key,
                    group_name=self._group,
                    consumer_name=self._consumer,
                    count=self._batch_size,
                    block_ms=self._block_ms,
                )

                if not messages:
                    now = time.time()
                    if now - self._last_pending_check >= self._pending_check_interval:
                        self._last_pending_check = now
                        messages = await self._stream.xreadgroup_typed(
                            stream_key=self._stream_key,
                            group_name=self._group,
                            consumer_name=self._consumer,
                            count=self._batch_size,
                            block_ms=1,
                            read_pending=True,
                        )

                    if not messages:
                        await asyncio.sleep(self._poll_interval)
                        continue

                ack_ids: list[str] = []
                for message in messages:
                    try:
                        handled = await self._handle_message(message.payload)
                        if handled:
                            ack_ids.append(message.msg_id)
                    except Exception as exc:
                        logger.error(f"处理结果消息失败: {exc}")

                if ack_ids:
                    await self._stream.xack(self._stream_key, ack_ids, self._group)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"结果消费循环异常: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _handle_message(self, task_status: data_pb2.TaskStatus) -> bool:
        """处理单条 ``TaskStatus`` 消息"""
        run_id = task_status.run_id
        if not run_id:
            return True

        # Proto Status enum → 小写字符串（与 task_run_service 现有 mapping 兼容）
        status_name = data_pb2.Status.Name(task_status.status)
        status = self._proto_status_to_str(status_name)

        # proto3 标量字段：未设置时默认 0。这里仍然把 0 透传给下游，
        # 由 task_run_service 决定是否覆盖。
        exit_code = task_status.exit_code
        error_message = task_status.error_message or ""

        # Timestamp 是 message 字段，可用 HasField 区分"已设置"和"零值默认"
        started_at = (
            _ts_to_datetime(task_status.started_at)
            if _safe_has_field(task_status, "started_at")
            else None
        )
        finished_at = (
            _ts_to_datetime(task_status.finished_at)
            if _safe_has_field(task_status, "finished_at")
            else None
        )

        duration_ms = task_status.duration_ms or None

        # Proto map<string, string> → dict，直接落 result_data
        result_data: dict[str, Any] = dict(task_status.data)

        return await task_run_service.update_result(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            data=result_data,
        )

    @staticmethod
    def _proto_status_to_str(status_name: str) -> str:
        """``STATUS_COMPLETED`` → ``completed``"""
        if status_name.startswith("STATUS_"):
            return status_name[len("STATUS_") :].lower()
        return status_name.lower()


def _safe_has_field(msg: Any, field_name: str) -> bool:
    """proto3 标量字段无 HasField，message 字段则有 — 统一封装。"""
    try:
        return msg.HasField(field_name)
    except (ValueError, AttributeError):
        return False


def _ts_to_datetime(ts: Any) -> datetime | None:
    """``common_pb2.Timestamp`` → ``datetime``，未设置时返回 None。"""
    if ts is None:
        return None
    seconds = getattr(ts, "seconds", 0)
    nanos = getattr(ts, "nanos", 0)
    if seconds == 0 and nanos == 0:
        return None
    return datetime.fromtimestamp(seconds + nanos / 1e9, tz=UTC)


result_loop = ResultLoop()

__all__ = ["ResultLoop", "result_loop"]
