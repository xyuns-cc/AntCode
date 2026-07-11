"""
结果消费循环

从 Redis Streams 消费 Worker 上报的执行结果并更新 TaskRun。

P1a 改造：Stream 上的消息现在是 Proto bytes（``data_pb2.TaskStatus``），
通过 ``StreamClient(codec=ProtoCodec(...))`` 自动解码为 typed 对象。

P2-#24 改造：解码失败（payload 损坏 / schema 不匹配 / 重复处理报错）
的消息现在不再被直接跳过——增加 ``XPENDING`` 检查的 deliver_count 阈值，
超过 ``MAX_DELIVER_COUNT`` 后 ``XACK`` 并 ``XADD`` 到 dead-letter
stream ``antcode:dead_letter:result``，避免坏消息无限重投阻塞 group。

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
from antcode_core.application.services.task_run_service import task_run_service
from antcode_core.infrastructure.redis import task_result_stream
from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from antcode_core.infrastructure.redis.stream_client import ProtoCodec, StreamClient
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from loguru import logger


def _dead_letter_stream_key(namespace: str | None = None) -> str:
    """死信队列 stream key：``antcode:dead_letter:result``。"""
    return f"{redis_namespace(namespace)}:dead_letter:result"


# 单条消息最大重投次数；超过后进入 DLQ。
MAX_DELIVER_COUNT = 5


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
                    # P1-19: typed decoder 现在会为解码失败的消息返回一个
                    # 带 decode_error 的 envelope（payload=None）。这类消息
                    # 直接走 DLQ,无需等 deliver_count 累计。
                    if getattr(message, "decode_error", None):
                        logger.error(
                            "结果消息解码失败 → 直接 DLQ: msg_id={} err={}",
                            message.msg_id,
                            message.decode_error,
                        )
                        if await self._move_to_dlq(message):
                            dlq_ids.append(message.msg_id)
                        # DLQ 写失败：不 ACK,留在 PEL 由下轮 XAUTOCLAIM 再试
                        continue

                    try:
                        handled = await self._handle_message(message.payload)
                        if handled:
                            ack_ids.append(message.msg_id)
                        elif await self._should_dead_letter(message.msg_id):
                            # P1-19: 只有 DLQ 写入成功才能 ACK,否则结果正文
                            # 会丢失。写失败保留 PEL 让 reclaim 循环再试。
                            if await self._move_to_dlq(message):
                                dlq_ids.append(message.msg_id)
                    except Exception:
                        logger.exception("处理结果消息失败: msg_id={}", message.msg_id)
                        if await self._should_dead_letter(message.msg_id):
                            if await self._move_to_dlq(message):
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
