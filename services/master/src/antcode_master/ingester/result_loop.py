"""
结果消费循环

从 Redis Streams 消费 Worker 上报的执行结果并更新 TaskRun。

P1a 改造：Stream 上的消息现在是 Proto bytes（``data_pb2.TaskStatus``），
通过 ``StreamClient(codec=ProtoCodec(...))`` 自动解码为 typed 对象。

P2-#24 改造：解码失败的消息写入 dead-letter stream
``antcode:dead_letter:result``。已成功解码的消息只有在处理函数明确返回
不可接受时才按 deliver_count 进入 DLQ；数据库、Redis、SSE 等依赖异常
始终保留在 PEL，恢复后继续处理，不能把合法最终结果永久 ACK 掉。

实现采取"直接用底层 redis 客户端做 XPENDING"的策略——``StreamClient``
当前不暴露 XPENDING；后续如果 StreamClient 增补 ``xpending`` 方法可
内联替换 ``_get_pending_deliver_count``。
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
from antcode_core.application.services.task_run_service import TaskRunService
from antcode_core.infrastructure.redis import task_result_stream
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.sse_event_stream import SSEEventPublishError
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from loguru import logger

from antcode_master.ingester.run_status_publisher import publish_persisted_run_status


def _dead_letter_stream_key(namespace: str | None = None) -> str:
    """死信队列 stream key：``antcode:dead_letter:result``。"""
    return f"{redis_namespace(namespace)}:dead_letter:result"


# 单条消息最大重投次数；超过后进入 DLQ。
MAX_DELIVER_COUNT = 5


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
        # P2-06: block_ms 从 5000 下调到 1000 —— XREADGROUP block 期间
        # asyncio 取消无法穿透到 Redis 端，5s block 会让 shutdown 阻塞 5s+;
        # 多个 loop asyncio.gather 停机时累积拖延更明显。1s 兼顾空转开销与
        # 关停响应速度。
        block_ms: int = 1000,
        batch_size: int = 50,
        pending_check_interval: int = 30,
    ):
        # E5: 默认 group 带 namespace，与其它 loop 保持一致
        from antcode_core.infrastructure.redis.control_plane import (
            result_consumer_group,
        )

        self._stream_key = stream_key or task_result_stream()
        self._group = group_name or result_consumer_group()
        # R1-P0-2 (审查报告): consumer 名去掉 id(self)——它每次重启进程都变，
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
        except Exception:
            # 已解码消息的依赖异常保留 PEL，不能按次数误判为 poison。
            logger.exception("处理结果消息失败，保留 PEL: msg_id={}", message.msg_id)
        return False, False

    async def _handle_message(self, task_status: data_pb2.TaskStatus) -> bool:
        """处理单条 ``TaskStatus`` 消息"""
        run_id = task_status.run_id
        if not run_id:
            return True

        # Proto Status enum → 小写字符串（E1: 复用 antcode_contracts.transcode 的
        # 权威 mapping，避免本地重复一份跑到 UNSPECIFIED 分叉）
        from antcode_contracts.transcode import proto_status_to_str

        status = proto_status_to_str(task_status.status)

        # proto3 标量字段：未设置时默认 0。这里仍然把 0 透传给下游，
        # 由 task_run_service 决定是否覆盖。
        exit_code = task_status.exit_code
        error_message = task_status.error_message or ""

        # Timestamp 是 message 字段，可用 HasField 区分"已设置"和"零值默认"
        started_at = _ts_to_datetime(task_status.started_at) if _safe_has_field(task_status, "started_at") else None
        finished_at = _ts_to_datetime(task_status.finished_at) if _safe_has_field(task_status, "finished_at") else None

        duration_ms = task_status.duration_ms or None

        # Proto map<string, string> → dict，直接落 result_data
        result_data: dict[str, Any] = dict(task_status.data)

        ok = await task_run_service.update_result(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            data=result_data,
            worker_id=task_status.worker_id,
        )

        # G4: 'killed' 从自动重试门槛移除 —— kill 是取消/运维介入的产物而非
        # 瞬态失败,不应触发自动重试;且 proto 契约本无 KILLED(worker 侧
        # killed 上行时已被 transcode 成 STATUS_FAILED),该分支实际不可达,
        # 保留只会误导。系统无独立的 cancel-request 持久标记,取消证据仅剩
        # DB 里的 CANCELLED 状态,由 _schedule_remote_retry 再核对。
        #
        # V5: retry 调度失败不再让已持久化的结果消息进 DLQ。
        # ``_schedule_remote_retry`` → ``scheduler_service._schedule_retry`` 是
        # durable-first:先在 TaskRun 行锁内写 ``next_retry_at`` 再投 Redis。
        # 因此 Redis 入队失败可由 ``retry_loop._recover_from_db`` 从
        # ``next_retry_at`` 恢复;结果本身(update_result)已落库,不应因重试
        # 入队失败而被 dead-letter 永久丢结果。捕获后照常 ACK + 推送状态。
        # 残留:若异常发生在 ``next_retry_at`` 落库之前(如重载查询/claim 事务
        # 失败),该 run 的自动重试会丢失;此时 DB 多半也不可用,原"留 PEL 重放"
        # 路径同样会在 MAX_DELIVER_COUNT 次后 dead-letter 丢失,交由 reconcile /
        # 人工兜底,不比旧行为差。
        if ok and status in ("failed", "timeout", "timed_out", "error"):
            try:
                await self._schedule_remote_retry(run_id)
            except Exception:
                logger.exception("远程失败重试调度失败: run_id={}", run_id)
                # P1-FN-04: 只有确认 durable intent 已落库（recover_from_db
                # 能接管）或该 run 本就不需要重试时才 ACK；否则保留 PEL 让
                # 结果消息重放，绝不静默丢失自动重试。
                if not await self._retry_intent_durable_or_ineligible(run_id):
                    logger.warning("重试意图无 durable 证据,保留 PEL 重放: run_id={}", run_id)
                    return False
        if ok:
            await publish_persisted_run_status(run_id)
        return ok

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
                .only("task_id", "retry_count", "next_retry_at", "status", "runtime_status")
                .first()
            )
            if execution is None:
                return True
            if execution.next_retry_at is not None:
                return True
            if execution.status == TaskStatus.CANCELLED or execution.runtime_status == RuntimeStatus.CANCELLED:
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
            .only("id", "run_id", "task_id", "retry_count", "result_data", "status", "runtime_status")
            .first()
        )
        if execution is None:
            return
        # G4: 用户取消与失败结果存在竞态 —— 取消 API 先发 control 再落库
        # CANCELLED,失败结果先落库时取消方 CAS 会输。这里基于重新加载的行,
        # 只要看到任一取消证据(overall status 或 runtime_status 为 CANCELLED)
        # 就跳过自动重试:用户已取消的 run 不应被复活。
        if execution.status == TaskStatus.CANCELLED or execution.runtime_status == RuntimeStatus.CANCELLED:
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

    # E1: 已弃用本地映射，改用 antcode_contracts.transcode.proto_status_to_str。
    # 旧方法保留兼容旧测试导入，转发到权威实现。
    @staticmethod
    def _proto_status_to_str(status_name: str) -> str:
        if status_name.startswith("STATUS_"):
            return status_name[len("STATUS_") :].lower()
        return status_name.lower()

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
                raw_bytes = payload.SerializeToString()
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
            await redis.xadd(
                _dead_letter_stream_key(),
                entry,
                maxlen=10_000,
                approximate=True,
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
