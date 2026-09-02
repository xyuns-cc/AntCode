"""Consume Worker results and durably settle TaskRun state.

Invalid payloads and explicitly rejected messages move to the dead-letter stream.
Messages are ACKed only after persistence and required downstream publication.
Failed retry scheduling is ACKed only when its
durable intent or ineligibility can be proven from PostgreSQL; otherwise the
message remains pending for replay.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from datetime import UTC, datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.application.services.lease_service import LeaseStore
from antcode_core.application.services.task_result_commit import ResultCommitOutcome
from antcode_core.application.services.task_run_service import TaskRunService
from antcode_core.application.services.workers.task_status_validation import BoundedTaskStatusCodec
from antcode_core.domain.models.enums import RuntimeStatus
from antcode_core.infrastructure.redis import task_result_stream
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.sse_event_stream import SSEEventPublishError
from antcode_core.infrastructure.redis.stream_client import StreamClient
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from loguru import logger

from antcode_master.ingester.result_dead_letter import insert_result_dead_letter, sanitized_task_status_bytes
from antcode_master.ingester.run_status_publisher import publish_persisted_run_status


def _dead_letter_stream_key(namespace: str | None = None) -> str:
    """死信队列 stream key：``antcode:dead_letter:result``。"""
    return f"{redis_namespace(namespace)}:dead_letter:result"


MAX_DELIVER_COUNT = 5
_RETRYABLE_RUNTIME_STATUSES = frozenset({RuntimeStatus.FAILED, RuntimeStatus.TIMEOUT})


class RetryIntentNotDurableError(RuntimeError):
    """A failed result cannot be ACKed until retry eligibility is durable."""


async def _validate_current_worker_lease(worker_id: str, lease_id: str) -> bool:
    if not worker_id or not lease_id:
        return False
    redis = await get_redis_client()
    store = LeaseStore(redis, namespace=redis_namespace())
    return await store.is_current(worker_id, lease_id)


task_run_service = TaskRunService(_validate_current_worker_lease)


class ResultLoop:
    """结果消费循环"""

    def __init__(
        self,
        stream_key: str | None = None,
        group_name: str | None = None,
        consumer_name: str | None = None,
        poll_interval: float = 1.0,
        block_ms: int = 1000,
        batch_size: int = 50,
        pending_check_interval: int = 30,
    ):
        from antcode_core.infrastructure.redis.control_plane import (
            result_consumer_group,
        )

        self._stream_key = stream_key or task_result_stream()
        self._group = group_name or result_consumer_group()
        # 老 consumer 名下的 PEL 永远读不回。用 hostname + pid 已经能区分
        # 同机多副本；重启后 pid 会变，靠周期 XAUTOCLAIM 认领旧 PEL（下面
        # _run_loop 加）。
        self._consumer = consumer_name or f"{socket.gethostname()}-{os.getpid()}"
        # XAUTOCLAIM min-idle 阈值（毫秒）：其他 consumer 空闲超过此时长的
        # PEL 消息会被本 consumer 认领重读。取一次 pending_check 周期 * 2，
        # 兼顾切主/重启场景，同时不与正常处理时长冲突。
        self._autoclaim_min_idle_ms = max(int(pending_check_interval * 2 * 1000), 30_000)
        self._last_autoclaim = 0.0
        self._poll_interval = poll_interval
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._pending_check_interval = pending_check_interval
        self._last_pending_check = 0.0
        self._stream = StreamClient(codec=BoundedTaskStatusCodec(data_pb2.TaskStatus))
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
        """主循环（异常时指数退避 + jitter，避免持续错误刷爆日志/连接池）"""
        from antcode_core.common.utils.retry import sleep_with_backoff

        consecutive_errors = 0
        while self._running:
            try:
                # T6-T2: 去掉 leader gate —— result 流走 XREADGROUP 消费组，
                # 每个 master 实例用 hostname-pid 作 consumer name，Redis
                # Streams 天然按 consumer 分区消息。多实例部署时 follower
                # 也承担 ingest 负载，不再干等 leader。
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
                        # R1-P0-2 (审查报告): XAUTOCLAIM 认领旧 consumer 的
                        # 悬挂 PEL 消息。之前 consumer 名带 id(self) 每次重启
                        # 都变，旧 PEL 永远读不回；即使我们把 consumer 名固定，
                        # 老部署里已经积压的旧 consumer PEL 也要被清理。
                        # 认领后消息进本 consumer 的 PEL，下一轮 read_pending
                        # 会捞出来处理。
                        if not messages:
                            try:
                                next_id = "0-0"
                                claimed_total = 0
                                for _ in range(3):  # 最多扫 3 页避免占用主循环
                                    next_id, claimed, _deleted = await self._stream.xautoclaim(
                                        self._stream_key,
                                        group_name=self._group,
                                        consumer_name=self._consumer,
                                        min_idle_time_ms=self._autoclaim_min_idle_ms,
                                        start_id=next_id,
                                        count=self._batch_size,
                                    )
                                    claimed_total += len(claimed)
                                    if not next_id or next_id == "0-0" or len(claimed) < self._batch_size:
                                        break
                                if claimed_total:
                                    logger.warning(
                                        "result_loop XAUTOCLAIM 认领旧 PEL {} 条",
                                        claimed_total,
                                    )
                            except Exception as exc:
                                logger.debug("XAUTOCLAIM 失败: {}", exc)

                    if not messages:
                        await asyncio.sleep(self._poll_interval)
                        continue

                ack_ids: list[str] = []
                dlq_ids: list[str] = []
                for message in messages:
                    should_ack, moved_to_dlq = await self._process_message(message)
                    if should_ack:
                        ack_ids.append(message.msg_id)
                    if moved_to_dlq:
                        dlq_ids.append(message.msg_id)

                # 正常处理完毕 + DLQ 后的消息都需要 ACK（防止重投阻塞 group）。
                # P1-19: dlq_ids 现在只包含 DLQ **写入成功** 的 msg_id;写入
                # 失败的会自然停留在 PEL,下一轮循环重试。
                final_ack_ids = ack_ids + dlq_ids
                if final_ack_ids:
                    await self._stream.xack(self._stream_key, final_ack_ids, self._group)
                    try:
                        client = await self._stream._get_client()
                        await trim_acknowledged_stream(
                            client,
                            self._stream_key,
                            self._group,
                        )
                    except Exception:
                        logger.exception("结果 Stream ACK 后裁剪失败")

                consecutive_errors = 0  # 一次成功迭代 → 清零

            except asyncio.CancelledError:
                break
            except Exception:
                consecutive_errors += 1
                logger.exception("结果消费循环异常（连续第 {} 次），指数退避中", consecutive_errors)
                # 1s → 2s → 4s → ... → 上限 60s，加 jitter
                await sleep_with_backoff(consecutive_errors, base_delay=1.0, max_delay=60.0)

    async def _process_message(self, message: Any) -> tuple[bool, bool]:
        """Return ``(ack, moved_to_dlq)`` for one result stream message."""
        decode_error = getattr(message, "decode_error", None)
        if decode_error:
            logger.error(
                "结果消息解码失败 → 直接 DLQ: msg_id={} err={}",
                message.msg_id,
                decode_error,
            )
            return False, await self._move_to_dlq(message)

        try:
            if await self._handle_message(message.payload):
                return True, False
            if await self._should_dead_letter(message.msg_id):
                return False, await self._move_to_dlq(message)
        except SSEEventPublishError:
            logger.exception(
                "结果状态实时事件发布失败，保留 PEL 重试: msg_id={}",
                message.msg_id,
            )
            return False, False
        except RetryIntentNotDurableError:
            logger.exception("重试意图尚未耐久，保留 PEL: msg_id={}", message.msg_id)
            return False, False
        except Exception:
            # 已解码消息的依赖异常保留 PEL，不能按次数误判为 poison。
            logger.exception("处理结果消息失败，保留 PEL: msg_id={}", message.msg_id)
        return False, False

    async def _handle_message(self, task_status: data_pb2.TaskStatus) -> bool:
        """处理单条 ``TaskStatus`` 消息"""
        if not task_status.run_id:
            return True
        outcome = await self._commit_task_status(task_status)
        await self._ensure_failed_result_retry(outcome)
        if outcome.accepted:
            await publish_persisted_run_status(outcome.run_id)
        return outcome.accepted

    async def _commit_task_status(self, task_status: data_pb2.TaskStatus) -> ResultCommitOutcome:
        """Decode one Worker report and return the database-authoritative outcome."""
        from antcode_contracts.transcode import proto_status_to_str

        started_at = _ts_to_datetime(task_status.started_at) if _safe_has_field(task_status, "started_at") else None
        finished_at = _ts_to_datetime(task_status.finished_at) if _safe_has_field(task_status, "finished_at") else None
        return await task_run_service.update_result_outcome(
            run_id=task_status.run_id,
            status=proto_status_to_str(task_status.status),
            exit_code=task_status.exit_code if task_status.HasField("exit_code") else None,
            error_message=task_status.error_message or "",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=task_status.duration_ms or None,
            data=dict(task_status.data),
            worker_id=task_status.worker_id,
        )

    async def _ensure_failed_result_retry(self, outcome: ResultCommitOutcome) -> None:
        if not outcome.accepted or outcome.runtime_status not in _RETRYABLE_RUNTIME_STATUSES:
            return
        try:
            await self._schedule_remote_retry(outcome.run_id)
        except Exception as exc:
            logger.exception("远程失败重试调度失败: run_id={}", outcome.run_id)
            if await self._retry_intent_durable_or_ineligible(outcome.run_id):
                return
            raise RetryIntentNotDurableError(f"重试意图尚未耐久: run_id={outcome.run_id}") from exc

    async def _retry_intent_durable_or_ineligible(self, run_id: str) -> bool:
        """重试调度异常后的证据核验（P1-FN-04）。

        True = 可以安全 ACK：durable intent 已写、或 run 已取消、或任务
        不允许/已耗尽重试。核验本身失败时保守返回 False（保留 PEL）。
        """
        try:
            from antcode_core.domain.models import Task, TaskRun
            from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus

            execution = (
                await TaskRun.filter(run_id=run_id)
                .only(
                    "task_id",
                    "retry_count",
                    "next_retry_at",
                    "status",
                    "runtime_status",
                    "cancel_requested_at",
                )
                .first()
            )
            if execution is None:
                return True
            durable_or_cancelled = (
                execution.next_retry_at is not None
                or execution.cancel_requested_at is not None
                or execution.status == TaskStatus.CANCELLED
                or execution.runtime_status == RuntimeStatus.CANCELLED
            )
            if durable_or_cancelled:
                return True
            task = await Task.filter(id=execution.task_id).only("retry_count").first()
            if task is None or not task.retry_count or task.retry_count <= 0:
                return True
            return execution.retry_count >= task.retry_count
        except Exception:
            logger.exception("重试意图核验失败,保守保留 PEL: run_id={}", run_id)
            return False

    async def _schedule_remote_retry(self, run_id: str) -> None:
        """P1-05：为远程 FAILED/TIMEOUT run 写 retry intent（若 task 允许）。

        绕开循环导入：延迟到函数体内 import scheduler_service。
        """
        from antcode_core.domain.models import Task, TaskRun
        from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus

        from antcode_master.control.scheduler_loop import scheduler_service

        execution = (
            await TaskRun.filter(run_id=run_id)
            .only(
                "id",
                "run_id",
                "task_id",
                "retry_count",
                "result_data",
                "status",
                "runtime_status",
                "cancel_requested_at",
            )
            .first()
        )
        if execution is None:
            return
        # G4: 取消请求与失败结果存在竞态。cancel_requested_at 是先于 control
        # 投递持久化的命令事实；即使 Worker 最终报告 FAILED，也不得自动复活。
        cancelled = execution.status == TaskStatus.CANCELLED or execution.runtime_status == RuntimeStatus.CANCELLED
        if execution.cancel_requested_at is not None or cancelled:
            logger.info("run 已取消,跳过自动重试: run_id={}", run_id)
            return
        task = (
            await Task.filter(id=execution.task_id)
            .only("id", "retry_count", "retry_delay", "is_active", "name")
            .first()
        )
        if task is None or not task.retry_count or task.retry_count <= 0:
            return
        await scheduler_service._schedule_retry(task, execution)

    async def _should_dead_letter(self, msg_id: str) -> bool:
        """判断单条消息的 deliver_count 是否已经超过阈值。

        StreamClient 暂未暴露 XPENDING，直接走 ``get_redis_client``。
        失败时返回 False，让消息留在 pending 由下轮 reclaim 处理。
        """
        try:
            redis = await get_redis_client()
            # XPENDING <stream> <group> <start> <end> <count> 返回
            # [[msg_id, consumer, idle_ms, deliver_count], ...]
            entries = await redis.xpending_range(
                name=self._stream_key,
                groupname=self._group,
                min=msg_id,
                max=msg_id,
                count=1,
            )
            if not entries:
                return False
            deliver_count = int(entries[0].get("times_delivered", 0))
            return deliver_count > MAX_DELIVER_COUNT
        except Exception:
            logger.exception("XPENDING 查询失败: msg_id={}", msg_id)
            return False

    async def _move_to_dlq(self, message) -> bool:
        """把一条坏掉的消息搬到 ``antcode:dead_letter:result``。

        DLQ 内消息仍是 Proto bytes，保留原 payload；同时附带 ``orig_msg_id``
        和 ``orig_stream`` 方便后续人工排障。

        P1-19: 返回 bool 而不是 None——**必须** 只在 DLQ 写入成功后调用方
        才可以 ACK。之前老实现里 DLQ 写异常被吞掉,调用方照样 ACK,结果
        正文彻底丢失。
        """
        try:
            redis = await get_redis_client()
            payload = message.payload
            # 解码失败的 envelope(payload=None + decode_error) 用 raw_fields
            # 里的原始 Proto bytes 兜底,别丢原始内容。
            if payload is not None and hasattr(payload, "SerializeToString"):
                raw_bytes = sanitized_task_status_bytes(payload)
            else:
                raw_fields = getattr(message, "raw_fields", None) or {}
                raw_bytes = raw_fields.get(b"p") or raw_fields.get("p") or b""
                if isinstance(raw_bytes, str):
                    raw_bytes = raw_bytes.encode("utf-8", errors="replace")
            entry: dict[str | bytes | int | float, bytes | str | int | float] = {
                "payload": raw_bytes,
                "orig_stream": self._stream_key,
                "orig_msg_id": message.msg_id,
                "moved_at_ms": str(int(time.time() * 1000)),
            }
            decode_error = getattr(message, "decode_error", None)
            if decode_error:
                entry["decode_error"] = decode_error
            await insert_result_dead_letter(
                redis,
                _dead_letter_stream_key(),
                source=self._stream_key,
                message_id=message.msg_id,
                entry=entry,
            )
            logger.warning(
                "消息进入 DLQ: msg_id={} stream={} decode_error={}",
                message.msg_id,
                self._stream_key,
                decode_error or "(runtime failure)",
            )
            return True
        except Exception:
            # P1-19: 关键告警。DLQ 长时间写不进会导致 PEL 无限增长,需要
            # 人工介入。这里用 exception 级别 log 保证不被吞。
            logger.exception(
                "DLQ 写入失败(将保留 PEL 由下轮 XAUTOCLAIM 再试): msg_id={}",
                message.msg_id,
            )
            return False


def _safe_has_field(msg: Any, field_name: str) -> bool:
    """proto3 标量字段无 HasField，message 字段则有 — 统一封装。"""
    try:
        return msg.HasField(field_name)
    except (ValueError, AttributeError):
        return False


# P1-round6 5.3: Timestamp 合理值域上限 = 9999-12-31T23:59:59 UTC (datetime.max)
_TS_MAX_SECONDS = 253402300799
_TS_NANOS_UPPER = 1_000_000_000


def _ts_to_datetime(ts: Any) -> datetime | None:
    """``common_pb2.Timestamp`` → ``datetime``，未设置时返回 None。

    P1-round6 5.3: seconds/nanos 极值(如 seconds=2^62)会让 datetime.fromtimestamp
    抛 OverflowError/ValueError, 之前直接向上传播到 result_loop 主循环, 消息可能
    被 catch + ACK 但不会真正结算(started_at/finished_at 缺失)。这里做值域校验,
    超范或非法就 log warning 返回 None(caller 会用 status_at fallback / status_at
    也不设的话由 update_result 用 datetime.now(UTC) 补), 保证极值不阻塞管道。
    """
    if ts is None:
        return None
    seconds = getattr(ts, "seconds", 0)
    nanos = getattr(ts, "nanos", 0)
    if seconds == 0 and nanos == 0:
        return None
    # datetime 支持范围: [1970-01-01, 9999-12-31]. Timestamp seconds 上限约 253402300799.
    if not (0 <= seconds < _TS_MAX_SECONDS) or not (0 <= nanos < _TS_NANOS_UPPER):
        logger.warning(
            "Timestamp 超合理范围, 视为未设置: seconds={} nanos={}",
            seconds,
            nanos,
        )
        return None
    try:
        return datetime.fromtimestamp(seconds + nanos / 1e9, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        logger.warning("Timestamp 转换失败, 视为未设置: seconds={} nanos={} err={}", seconds, nanos, exc)
        return None


result_loop = ResultLoop()

__all__ = ["ResultLoop", "result_loop"]
