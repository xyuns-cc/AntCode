"""
Redis 传输层实现（Direct 模式）

内网 Worker 直连 Redis Streams，低延迟。

wire format：
- ``report_result`` 写 ``task:result`` Stream 用 Proto bytes（``data_pb2.TaskStatus``
  序列化到 ``PROTO_FIELD``），与 Master ``result_loop`` 的 ``ProtoCodec`` 对齐。
- ``send_log`` / ``send_log_batch`` 写 log Stream 用 Proto bytes（``data_pb2.LogBatch``），
  与 Master ``log_ingest_loop`` 对齐。
- ``poll_control`` / ``send_control_result`` 仍是 dict/JSON。
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from antcode_worker.transport.redis.direct_control import DirectControlClient

from antcode_contracts import data_pb2
from antcode_contracts.transcode import encode_task_status
from antcode_core.application.services.ready_dispatch_generation import require_dispatch_generation
from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.common.log_limits import LogBatchLimits
from antcode_core.common.security.task_payload_diagnostics import redact_persisted_task_frame
from antcode_core.common.security.task_payload_envelope import open_ready_payload
from antcode_core.infrastructure.redis import decode_stream_payload
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from antcode_core.observability.tracing import inject_trace
from antcode_core.spider_retention import SpiderRetention
from loguru import logger
from redis.exceptions import ConnectionError, TimeoutError

from antcode_worker.domain.models import SourceBundle
from antcode_worker.transport import task_message_validation
from antcode_worker.transport.base import (
    ControlMessage,
    GenerationLostError,
    HeartbeatMessage,
    LogMessage,
    ServerConfig,
    TaskMessage,
    TaskResult,
    TransportBase,
    TransportMode,
    WorkerState,
)
from antcode_worker.transport.gateway.heartbeat_metrics import heartbeat_metrics_dict
from antcode_worker.transport.generation import raise_if_generation_lost
from antcode_worker.transport.redis.deferred_recovery import DeferredTaskRecovery
from antcode_worker.transport.redis.heartbeat_hash import write_legacy_heartbeat_hash
from antcode_worker.transport.redis.keys import RedisKeys
from antcode_worker.transport.redis.lease_generation import LeaseGenerationMixin
from antcode_worker.transport.redis.owned_stream_ack import ack_owned_stream_entry, describe_ack_failure
from antcode_worker.transport.redis.pending_recovery import PendingMessageRecovery
from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer, ensure_consumer_group
from antcode_worker.transport.redis.runtime_control import (
    ControlChannel,
    ControlSource,
    PendingControlRecovery,
    RuntimeControlResult,
    recover_runtime_control_settlement,
    settle_runtime_control_result,
)
from antcode_worker.transport.redis.runtime_control_evidence import (
    SettlementEvidenceError,
    validate_runtime_request,
)
from antcode_worker.transport.redis.spider_payload import normalize_spider_items
from antcode_worker.transport.redis.task_settlement import ack_owned_task, requeue_owned_task

DEFAULT_RECLAIMED_QUEUE_CAPACITY = 10
MILLISECONDS_PER_SECOND = 1_000


# P1: Status / LogType / Timestamp 转换全部走 ``antcode_contracts.transcode``
# （原来这里有本地 ``_STATUS_STR_TO_PROTO`` / ``_log_type_str_to_proto`` /
# ``_datetime_to_proto_timestamp`` 三份拷贝，与 gateway/codecs.py 重复）。
class RedisTransport(LeaseGenerationMixin, TransportBase):
    """
    Redis 传输层实现

    内网 Worker 直连 Redis Streams，提供：
    - 任务拉取：从 ready queue 读取任务
    - 任务确认：ACK 消息
    - 结果上报：写入 result stream
    - 日志发送：写入 log stream
    - 心跳上报：写入 heartbeat hash

    Requirements: 5.3, 7.2, 11.3
    """

    def __init__(
        self,
        redis_url: str | None = None,
        worker_id: str | None = None,
        config: ServerConfig | None = None,
        *,
        namespace: str | None = None,
        consumer_group: str | None = None,
        control_group: str | None = None,
        direct_control: "DirectControlClient | None" = None,
        task_payload_secret: str | None = None,
        reclaimed_queue_capacity: int = DEFAULT_RECLAIMED_QUEUE_CAPACITY,
    ):
        if not redis_url:
            raise ValueError("redis_url 必须显式配置")
        super().__init__(config)
        self._redis_url = redis_url
        self._direct_control = direct_control
        self._task_payload_secret = task_payload_secret or ""
        self._redis: Redis | None = None
        self._worker_id = worker_id
        resolved_namespace = namespace or getattr(self._config, "redis_namespace", None)
        self._keys = RedisKeys(namespace=resolved_namespace)
        self._spider_retention = SpiderRetention.from_env(
            stream_max_len_env="ANTCODE_SPIDER_STREAM_MAXLEN",
            ttl_seconds_env="ANTCODE_SPIDER_META_TTL_SECONDS",
        )
        self._consumer_group = consumer_group or self._keys.consumer_group_name()
        self._consumer_name = self._keys.consumer_name(worker_id) if worker_id else "worker"
        self._task_consumer_name = self._consumer_name
        self._control_consumer_name = self._consumer_name
        self._control_group = control_group or self._keys.consumer_group_name("control")
        self._control_channels = self._build_control_channels()
        self._control_recovery = PendingControlRecovery(
            self._control_channels,
            legacy_consumer_name=self._consumer_name,
            require_current_generation=self._require_current_generation,
            is_in_flight=lambda stream_key, message_id: (stream_key, message_id) in self._control_in_flight,
        )
        self._task_recovery = PendingMessageRecovery(
            self._keys.task_ready_stream(worker_id),
            self._consumer_group,
        )
        self._reclaimer: PendingTaskReclaimer | None = None
        # start / stop / reconnect 共享同一生命周期锁，确保显式 shutdown
        # 不会被并发 reconnect 复活，也不会清掉刚创建的新 session。
        self._reconnect_lock = asyncio.Lock()
        self._receipt_cache: dict[str, tuple[str, str, dict[str, Any]]] = {}
        # 已投递未 ACK 的 control 条目；PEL 恢复必须跳过，理由见 pending_recovery。
        self._control_in_flight: set[tuple[str, str]] = set()
        self._poll_error_count = 0
        self._poll_backoff_until = 0.0
        # R1-P0-4: 被 reclaimer 认领回来的消息塞在这里，poll_task 会优先消费
        # 队列元素形如 (msg_id, decoded_data)
        if isinstance(reclaimed_queue_capacity, bool) or reclaimed_queue_capacity < 1:
            raise ValueError("reclaimed_queue_capacity 必须是正整数")
        self._reclaimed_queue: asyncio.Queue = asyncio.Queue(maxsize=reclaimed_queue_capacity)
        # Lease 本地只读状态：写入走可信控制面，能力快照由 wiring 注入；_generation_lost 置位后拒绝新代际（P1-GW-03）。
        self._lease_store: Any = None
        self._lease_id: str = ""
        self._lease_fencing_enabled = False
        self._generation_lost = False
        self._lease_revoked_callback: Any | None = None
        self._lease_capabilities: dict[str, str] = {}
        self._deferred_recovery = DeferredTaskRecovery(
            redis_provider=lambda: self._redis,
            consumer_group=self._consumer_group,
            current_consumer_name=lambda: self._task_consumer_name,
            generation_guard=self._is_current_generation,
            on_visible=self._enqueue_reclaimed,
        )

    def _is_connection_error(self, exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        return "Connection closed" in str(exc)

    async def _run_with_reconnect(self, op_name: str, operation):
        try:
            return await operation()
        except Exception as e:
            if not self._is_connection_error(e):
                raise
            logger.warning(f"{op_name} 遇到 Redis 连接异常，尝试重连: {e}")
            if not await self.reconnect():
                raise
            return await operation()

    @property
    def mode(self) -> TransportMode:
        return TransportMode.DIRECT

    async def start(self) -> bool:
        async with self._reconnect_lock:
            return await self._start_locked()

    async def _start_locked(self) -> bool:
        """启动 Redis 连接"""
        if self._running:
            return True

        if not self._worker_id:
            logger.error("worker_id 未配置，无法启动 Redis 传输层")
            return False

        # T6-T1: 走统一 factory，自动分派 standalone / cluster / sentinel。
        # 重试/keepalive 参数已内建在 factory 里，这里只留连接建立重试。
        from antcode_core.infrastructure.redis.factory import (
            create_async_redis_client,
        )

        max_attempts = min(3, max(1, self._config.max_reconnect_attempts))
        delay = 0.3

        for attempt in range(1, max_attempts + 1):
            try:
                self._redis = create_async_redis_client(
                    self._redis_url,
                    decode_responses=True,
                )
                redis = self._redis

                # 测试连接
                await cast(Awaitable[Any], redis.ping())

                # 确保消费者组存在
                ready_stream = self._keys.task_ready_stream(self._worker_id)
                await ensure_consumer_group(redis, ready_stream, self._consumer_group)

                # 控制通道消费者组
                await ensure_consumer_group(redis, self._keys.control_stream(self._worker_id), self._control_group)
                self._control_recovery.reset()
                self._task_recovery.reset()

                from antcode_core.application.services.lease_service import (
                    LeaseStore,
                    wire_lease_policy,
                )

                self._lease_store = LeaseStore(
                    redis_client=self._redis,
                    namespace=self._keys.namespace,
                    policy=wire_lease_policy(),
                )
                self._lease_fencing_enabled = True

                # 启动 pending 回收器
                # R1-P0-4 (审查报告)：认领到的消息通过 on_reclaimed 回调塞进
                # 本地 _reclaimed_queue，poll_task 优先消费该队列 → 消息回到
                # engine 执行。原实现丢弃 _do_reclaim 返回值，认领等于空转。
                self._reclaimer = PendingTaskReclaimer(
                    redis_client=self._redis,
                    worker_id=self._worker_id,
                    keys=self._keys,
                    consumer_group=self._consumer_group,
                    generation_guard=self._is_current_generation,
                    current_consumer_name=lambda: self._task_consumer_name,
                    on_reclaimed=self._enqueue_reclaimed,
                    on_delivery_failed=self._task_recovery.reset,
                    available_capacity=self._reclaimed_queue_available_capacity,
                )
                await self._reclaimer.start()
                await self._deferred_recovery.start()

                self._running = True
                await self._set_state(WorkerState.ONLINE)

                # P1-10: 明文 Redis URL 含 ACL 用户名/密码,禁止直接落 INFO 日志
                _masked = self._redis_url
                if "@" in _masked:
                    _prefix, _suffix = _masked.split("@", 1)
                    if ":" in _prefix:
                        _prefix = _prefix.rsplit(":", 1)[0] + ":***"
                    _masked = f"{_prefix}@{_suffix}"
                logger.info(f"Redis 传输层已启动: {_masked}")
                return True

            except Exception as e:
                await self._cleanup_failed_start()
                if not self._is_connection_error(e) or attempt >= max_attempts:
                    logger.exception("Redis 连接失败")
                    return False
                logger.warning(f"Redis 连接失败，{delay:.1f}s 后重试 ({attempt}/{max_attempts})")
                await asyncio.sleep(delay)
                delay = min(2.0, delay * 2)

        return False

    async def _cleanup_failed_start(self) -> None:
        redis = self._redis
        reclaimer = self._reclaimer
        self._redis = None
        self._reclaimer = None
        self._running = False
        cleanup_steps = [self._deferred_recovery.stop()]
        if reclaimer:
            cleanup_steps.append(reclaimer.stop())
        if redis:
            cleanup_steps.append(redis.aclose())
        await asyncio.gather(*cleanup_steps)

    async def stop(self, grace_period: float = 5.0) -> None:
        async with self._reconnect_lock:
            await self._stop_locked(close_control=True)

    async def _stop_locked(self, *, close_control: bool) -> None:
        """停止 Redis 连接"""
        if not self._running and self._redis is None and self._reclaimer is None:
            if close_control and self._direct_control:
                await self._direct_control.aclose()
            return

        self._running = False
        redis = self._redis
        reclaimer = self._reclaimer
        self._redis = None
        self._reclaimer = None
        shutdown_steps = []
        if reclaimer:
            shutdown_steps.append(reclaimer.stop())
        shutdown_steps.append(self._deferred_recovery.stop())
        if redis:
            shutdown_steps.append(redis.aclose())
        if close_control and self._direct_control:
            shutdown_steps.append(self._direct_control.aclose())
        if shutdown_steps:
            await asyncio.gather(*shutdown_steps)

        await self._set_state(WorkerState.OFFLINE)
        logger.info("Redis 传输层已停止")

    async def deregister(self, reason: str = "shutdown") -> None:
        """Ask the trusted control plane to revoke the current Direct lease."""
        if not self._worker_id or not self._lease_id:
            return
        if self._direct_control is None:
            raise RuntimeError("Direct control client 未配置，无法 deregister")
        revoked = await self._direct_control.deregister(self._lease_id, reason)
        if not revoked:
            logger.warning("跳过旧 generation deregister: worker_id={}", self._worker_id)
            return
        logger.info(f"worker 主动 deregister 完成 (reason={reason})")

    async def claim_run_ownership(self, run_id: str, ttl_ms: int) -> bool:
        if self._direct_control is None:
            raise RuntimeError("Direct control client 未配置，无法 claim run ownership")
        await self._require_current_generation()
        return await self._direct_control.claim_run_ownership(self._lease_id, run_id, ttl_ms)

    async def renew_run_ownership(self, run_id: str, ttl_ms: int) -> bool:
        if self._direct_control is None:
            raise RuntimeError("Direct control client 未配置，无法 renew run ownership")
        await self._require_current_generation()
        return await self._direct_control.renew_run_ownership(self._lease_id, run_id, ttl_ms)

    async def release_run_ownership(self, run_id: str) -> bool:
        if self._direct_control is None:
            raise RuntimeError("Direct control client 未配置，无法 release run ownership")
        await self._require_current_generation()
        return await self._direct_control.release_run_ownership(self._lease_id, run_id)

    async def _enqueue_reclaimed(self, msg_id: str, data: dict[str, str]) -> None:
        """R1-P0-4: reclaimer 的 on_reclaimed 回调。

        认领到的消息塞进本地队列；poll_task 会优先消费。data 已经是 Redis
        原始 field map（bytes 或 str），走 _decode_data 与正常 poll 一致。
        """
        await self._reclaimed_queue.put((msg_id, data))

    def _reclaimed_queue_available_capacity(self) -> int:
        return self._reclaimed_queue.maxsize - self._reclaimed_queue.qsize()

    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """
        从 Redis Streams 拉取任务

        使用 XREADGROUP 从 ready queue 读取任务。R1-P0-4: 优先消费本地
        _reclaimed_queue（reclaimer 认领的旧 PEL 消息）。
        """
        if not self._redis or not self._running:
            return None

        await self._require_current_generation()

        # R1-P0-4: 优先出被认领的旧消息
        reclaimed: tuple[str, dict[str, str]] | None = None
        try:
            reclaimed = self._reclaimed_queue.get_nowait()
        except asyncio.QueueEmpty:
            reclaimed = None

        try:
            stream_key = self._keys.task_ready_stream(self._worker_id)
            if reclaimed is not None:
                msg_id, data = reclaimed
                return await self._build_guarded_task(stream_key, msg_id, data)

            pending = await self._task_recovery.poll(self._redis, self._task_consumer_name)
            if pending is not None:
                msg_id, data = pending
                return await self._build_guarded_task(stream_key, msg_id, data)

            now = time.monotonic()
            if self._poll_backoff_until > now:
                await asyncio.sleep(self._poll_backoff_until - now)

            result = await self._redis.xreadgroup(
                groupname=self._consumer_group,
                consumername=self._task_consumer_name,
                streams={stream_key: ">"},
                count=1,
                block=int(timeout * 1000),
            )

            self._poll_error_count = 0
            self._poll_backoff_until = 0.0

            if not result:
                return None

            # 解析消息
            stream_name, messages = result[0]
            if not messages:
                return None

            msg_id, data = messages[0]
            return await self._build_guarded_task(stream_name, msg_id, data)

        except Exception:
            self._task_recovery.reset()
            self._poll_error_count += 1
            delay = min(30.0, 0.5 * (2 ** (self._poll_error_count - 1)))
            self._poll_backoff_until = time.monotonic() + delay
            logger.exception("拉取任务失败")
            logger.warning(f"拉取任务退避 {delay:.1f}s (连续失败 {self._poll_error_count} 次)")
            if self._poll_error_count % 3 == 0:
                await self.reconnect()
            raise

    # 解码/构造类失败 = 坏帧；连接、代际类异常仍走 poll 重试。
    _BAD_FRAME_ERRORS = (ValueError, KeyError, TypeError)

    async def _build_guarded_task(
        self,
        stream_name: str,
        message_id: str,
        data: dict[Any, Any],
    ) -> TaskMessage | None:
        await self._require_current_generation()
        try:
            decoded = self._decode_data(data)
            require_dispatch_generation(decoded, self._lease_id)
            task_message_validation.validate_task_message_public_fields(decoded, allow_integer_strings=True)
            persisted = dict(decoded)
            decoded = open_ready_payload(
                decoded,
                worker_id=self._worker_id or "",
                worker_secret=self._task_payload_secret,
            )
            receipt = self._encode_receipt(stream_name, message_id)
            message = self._build_task_message(stream_name, message_id, decoded, receipt)
            self._receipt_cache[receipt] = (stream_name, message_id, persisted)
        except self._BAD_FRAME_ERRORS as exc:
            # P1-DR-02: 坏帧此前只 reset recovery，又被 pending recovery
            # 反复重投（reclaim_generation 跳过当前 consumer），单个事件即可
            # 永久堵塞任务面。现在当前代际直接 DLQ + ACK（幂等 settlement），
            # 保留原始帧证据并解放 PEL。DLQ 写失败则冒泡，消息留 PEL 重试。
            await self._dead_letter_bad_task_frame(stream_name, message_id, data, exc)
            return None
        self._poll_error_count = 0
        self._poll_backoff_until = 0.0
        return message

    async def _dead_letter_bad_task_frame(
        self,
        stream_name: str,
        message_id: str,
        data: dict[Any, Any],
        error: Exception,
    ) -> None:
        if self._reclaimer is None:
            raise RuntimeError(f"坏任务帧无法 DLQ（reclaimer 未启动）: stream={stream_name} msg={message_id}")
        logger.error(
            "坏任务帧进 DLQ: stream={} msg={} error={}: {}",
            stream_name,
            message_id,
            type(error).__name__,
            error,
        )
        payload = redact_persisted_task_frame(
            {self._settlement_text(k): self._settlement_text(v) for k, v in (data or {}).items()}
        )
        payload["_bad_frame_error"] = f"{type(error).__name__}: {error}"
        await self._reclaimer.dead_letter_owned(stream_name, message_id, payload)

    @staticmethod
    def _settlement_text(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _build_task_message(
        self,
        stream_name: str,
        msg_id: str,
        decoded: dict[str, Any],
        receipt: str,
    ) -> TaskMessage:
        """Build a TaskMessage for poll and reclaim paths."""
        source_bundle = self._decode_source_bundle(decoded)
        fields = task_message_validation.validate_task_message_payload(decoded, allow_integer_strings=True)

        task_msg = TaskMessage(
            task_id=fields.task_id,
            project_id=fields.project_id,
            project_type=fields.project_type,
            priority=fields.priority,
            params=fields.params,
            environment=fields.environment,
            timeout=fields.timeout,
            source_bundle=source_bundle,
            source_subdir=fields.source_subdir,
            entry_point=fields.entry_point,
            runtime_env_name=fields.runtime_env_name,
            run_id=fields.run_id,
            receipt=receipt,
        )

        traceparent = decoded.get("trace_parent") or decoded.get("traceparent") or ""
        if traceparent:
            task_msg.traceparent = traceparent  # type: ignore[attr-defined]

        return task_msg

    async def ack_task(self, task_id: str, accepted: bool, reason: str = "") -> bool:
        """确认任务"""
        if not self._redis or not self._running:
            return False

        try:
            await self._require_current_generation()
            if not accepted:
                return await self.requeue_task(task_id, reason=reason)

            stream_key, msg_id, _data = self._task_source(task_id)
            await self._require_current_generation()
            acknowledged = await self._run_with_reconnect(
                "确认任务",
                lambda: ack_owned_task(
                    self._redis,
                    stream_key=stream_key,
                    group=self._consumer_group,
                    message_id=msg_id,
                    consumer_name=self._task_consumer_name,
                ),
            )
            if int(acknowledged) < 0:
                self._task_recovery.reset()
                return False
            try:
                await trim_acknowledged_stream(
                    self._redis,
                    stream_key,
                    self._consumer_group,
                )
            except Exception:
                logger.exception("任务 ACK 后裁剪失败: stream={}", stream_key)
            self._receipt_cache.pop(task_id, None)
            return True

        except Exception as exc:
            raise_if_generation_lost(exc)
            self._task_recovery.reset()
            logger.exception("确认任务失败")
            return False

    async def defer_task(self, receipt: str, reason: str = "") -> bool:
        """登记精确 receipt，到可见时间后恢复当前 consumer 的该条 PEL。"""
        try:
            await self._require_current_generation()
            stream_key, message_id, _data = self._task_source(receipt)
            await self._deferred_recovery.defer(
                stream_key=stream_key,
                message_id=message_id,
                consumer_name=self._task_consumer_name,
            )
        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.exception("登记 deferred task 恢复失败: receipt={}", receipt)
            return False
        self._receipt_cache.pop(receipt, None)
        logger.info(f"任务保留在 PEL 等待重投: receipt={receipt} reason={reason}")
        return True

    async def report_result(self, result: TaskResult) -> bool:
        """上报任务结果

        P1b：从 dict 切换到 Proto bytes（``data_pb2.TaskStatus``），写到
        ``task:result`` Stream 的单字段 ``PROTO_FIELD``。Master ``result_loop``
        通过 ``ProtoCodec(TaskStatus)`` 解码。

        P1-25 修复: 之前无脑 ``await xadd(...); return True`` —— 只要没抛
        异常就返回 True，但 XADD 返回 ``None`` / 空 msg id（极少见，例如
        pipeline 内部报错被 lib 吞）时就无声“成功”了，然后 engine 会 XACK
        掉源 ready-stream 消息，结果彻底丢。改为显式取 msg id，falsy 就
        返回 False，让 engine 保留本地状态、不 XACK，等下一轮 reclaim。
        Redis XADD 是同步语义（redis-py 返回时 server 已经写入并回 ack），
        所以只要拿到非空 msg id 就等价于服务端持久化确认。
        """
        if not self._redis or not self._running:
            return False

        try:
            await self._require_current_generation()
            result_key = self._keys.task_result_stream()
            result_data = dict(result.data or {})
            if self._lease_id:
                producing_lease = str(result_data.get("lease_id") or "")
                if producing_lease and producing_lease != self._lease_id:
                    raise ValueError("TaskResult lease_id 与当前 Direct generation 冲突")
                result_data["lease_id"] = self._lease_id
            ts_msg = encode_task_status(
                run_id=result.run_id or "",
                task_id=result.task_id or "",
                worker_id=self._worker_id or "",
                status=result.status,
                exit_code=int(result.exit_code or 0),
                error_message=normalize_persisted_error_message(result.error_message) or "",
                started_at=result.started_at,
                finished_at=result.finished_at,
                duration_ms=int(result.duration_ms or 0),
                data=result_data,
            )

            # P5.4: 透传当前 trace(由 engine ``_worker_loop`` set_current_trace
            # 绑定)。无 contextvar 时 inject_trace 会兜底生成新 trace,确保
            # task:result Stream 上的每条 TaskStatus 都带 trace。
            inject_trace(ts_msg)

            payload_bytes = ts_msg.SerializeToString()

            msg_id = await self._run_with_reconnect(
                "上报结果",
                lambda: self._redis.xadd(
                    result_key,
                    {PROTO_FIELD: payload_bytes},
                ),
            )
            if not msg_id:
                # P1-25: 显式失败, 不 return True。
                logger.error(
                    f"report_result XADD 未返回 msg_id, 视为持久化失败: run_id={result.run_id} task_id={result.task_id}"
                )
                return False
            # P1-GW-04 (round6): XADD 后代际若切换, 主动 XDEL 掉刚写入的 msg_id,
            # 避免 master 侧 lease_validator 之外还有孤儿消息在 stream 里堆积;
            # XDEL 失败不阻塞 (master 侧 fence 已够), 只 warn。
            if not await self._is_current_generation():
                try:
                    await self._redis.xdel(result_key, msg_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("代际已切换但 XDEL 撤销 orphan result 失败: msg_id={} err={}", msg_id, exc)
                raise GenerationLostError("Direct Worker lease generation 在 XADD 后失效")
            return True

        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.exception("上报结果失败")
            return False

    # U6: requeue 病毒消息阈值。
    MAX_REQUEUE_COUNT = 5

    async def requeue_task(self, receipt: str, reason: str = "") -> bool:
        """Atomically requeue an owned task or settle it through the shared DLQ protocol."""
        if not self._redis or not self._running:
            return False

        try:
            await self._require_current_generation()
            stream_key, msg_id, data = self._task_source(receipt)
            data = dict(data)
            # U6: 毒消息防御——requeue_count 字段被污染成非数字时按 0 处理，
            # 否则这里 ValueError → 外层 except 返回 False，消息永远卡在
            # PEL 里等 reclaimer 兜底，requeue/DLQ 通路直接断掉。
            try:
                current_count = int(data.get("requeue_count", "0") or "0")
            except (TypeError, ValueError):
                current_count = 0
            if current_count < 0:
                await self._dead_letter_invalid_requeue_count(
                    stream_key,
                    msg_id,
                    data,
                    value=current_count,
                )
                self._receipt_cache.pop(receipt, None)
                return True
            requeue_count = current_count + 1
            if requeue_count > self.MAX_REQUEUE_COUNT:
                if self._reclaimer is None:
                    raise RuntimeError("Direct task reclaimer 未启动，无法写入统一 DLQ")
                await self._reclaimer.dead_letter_owned(
                    stream_key,
                    msg_id,
                    self._serialize_for_xadd(data),
                )
                self._receipt_cache.pop(receipt, None)
                logger.warning(
                    f"消息进入死信(超过 {self.MAX_REQUEUE_COUNT} 次 requeue): "
                    f"receipt={receipt} count={requeue_count} reason={reason}"
                )
                return True
            payload = self._serialize_for_xadd(data)
            payload["requeue_count"] = str(requeue_count)
            payload["requeue_reason"] = reason or ""
            await self._require_current_generation()
            await self._run_with_reconnect(
                "任务重新入队",
                lambda: requeue_owned_task(
                    self._redis,
                    stream_key=stream_key,
                    group=self._consumer_group,
                    message_id=msg_id,
                    consumer_name=self._task_consumer_name,
                    payload=payload,
                ),
            )
            self._receipt_cache.pop(receipt, None)
            return True
        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.exception("重新入队失败")
            return False

    async def _dead_letter_invalid_requeue_count(
        self,
        stream_key: str,
        message_id: str,
        data: dict[str, Any],
        *,
        value: int,
    ) -> None:
        if self._reclaimer is None:
            raise RuntimeError("Direct task reclaimer 未启动，无法隔离非法 requeue_count")
        payload = self._serialize_for_xadd(data)
        payload["_bad_frame_error"] = f"requeue_count 必须是非负整数，实际为 {value}"
        await self._reclaimer.dead_letter_owned(stream_key, message_id, payload)

    def _task_source(self, receipt: str) -> tuple[str, str, dict[str, Any]]:
        stream_key, message_id = self._decode_receipt(receipt)
        expected_stream = self._keys.task_ready_stream(self._worker_id)
        cached = self._receipt_cache.get(receipt)
        if not message_id or stream_key != expected_stream or cached is None:
            raise ValueError("Direct task receipt 不属于当前 Worker")
        cached_stream, cached_message_id, data = cached
        if cached_stream != stream_key or cached_message_id != message_id:
            raise ValueError("Direct task receipt 与本地交付记录冲突")
        return stream_key, message_id, data

    def _serialize_for_xadd(self, data: dict[str, Any]) -> dict[str, str]:
        """把缓存中的解码后 dict 还原成 ``xadd`` 可接受的 ``str/bytes`` map。

        ``_decode_data`` 把 ``params/environment/data/payload`` 之类的字符串
        字段 ``json.loads`` 成了 Python dict。``xadd`` 不接受 nested dict
        作为字段值(redis-py 默认 ``str(dict)``,下游解析直接炸),所以这里
        显式 ``json.dumps`` 一次。其它字段统一 ``str(...)``。
        """
        out: dict[str, str] = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                out[k] = "true" if v else "false"
            elif v is None:
                out[k] = ""
            elif isinstance(v, (bytes, str)):
                out[k] = v if isinstance(v, str) else v.decode("utf-8")
            else:
                out[k] = str(v)
        return out

    async def send_log(self, log: LogMessage) -> bool:
        """发送实时日志

        接口语义保留：单条 log → 1-entry ``LogBatch``，落到日志 Stream。
        """
        # 内部转批，避免两条编码路径走样
        return await self.send_log_batch([log])

    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        """构批与 Gateway 共用 ``transport.log_batches``（P1-DR-03/P1-GW-03）。

        - 同一 8 MiB 批 / 1 MiB 单行预算：超限在生产端显式 ``ValueError``，
          不再出现"XADD 成功、Master 消费端拒收进 DLQ"的契约不一致。
        - 内容确定性 ``batch_id``：XADD 响应丢失后重发得到相同 event_id，
          Master 侧唯一索引去重。
        """
        if not self._redis or not self._running:
            return False
        if not logs:
            return True

        try:
            await self._require_current_generation()
            batches = self._build_log_batch_protos(logs)
            results = await self._report_log_batches(batches)
            await self._require_current_generation()
            return self._log_batch_results_succeeded(results, len(batches))
        except (GenerationLostError, ValueError):
            # 永久无效输入（如单行超 1 MiB）必须冒泡，BatchSender 据此
            # 终止发送循环；代际丢失则由 Engine 触发 self-fence。
            raise
        except Exception:
            logger.exception("发送批量日志失败")
            return False

    def _build_log_batch_protos(self, logs: list[LogMessage]) -> list[data_pb2.LogBatch]:
        from antcode_worker.transport.log_batches import build_log_batches

        if not self._lease_id:
            raise RuntimeError("Direct 日志批次缺少当前 lease_id")
        return build_log_batches(
            logs,
            worker_id=self._worker_id or "",
            lease_id=self._lease_id,
            limits=LogBatchLimits(),
        )

    async def _report_log_batches(self, batches: list[data_pb2.LogBatch]) -> list[bool]:
        if self._direct_control is None:
            raise RuntimeError("Direct 日志上报缺少可信控制面客户端")
        results = []
        for batch in batches:
            results.append(await self._direct_control.report_log_batch(batch.SerializeToString()))
        return results

    def _log_batch_results_succeeded(self, results: list[Any], expected: int) -> bool:
        if len(results) != expected:
            logger.error("发送批量日志返回数量不匹配")
            return False
        for result in results:
            if isinstance(result, Exception) or not result:
                logger.error(f"发送批量日志失败: {result}")
                return False
        return True

    async def send_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """发送心跳 — P3 桥接到 ``lease_renew``。

        Direct 模式没有 gRPC，统一走 ``LeaseStore.grant``。对外仍叫
        ``send_heartbeat`` 是因为 ``HeartbeatReporter`` 还在调用本名字。
        metrics 由 ``lease_renew`` 内部同时写到 ``heartbeat:{worker_id}``
        Hash，保留运维 dashboard 视图。
        """
        if not self._running:
            return False

        worker_id = getattr(heartbeat, "worker_id", None) or self._worker_id
        if not worker_id:
            logger.warning("发送心跳失败: 缺少 worker_id")
            return False

        metrics_dict = self._heartbeat_to_metrics_dict(heartbeat)
        try:
            _new_lease, _exp, _renew, revoked = await self.lease_renew(
                current_lease_id=self._lease_id,
                metrics=metrics_dict,
            )
            if revoked:
                logger.warning(f"心跳收到 lease revoked 信号: worker_id={worker_id}")
                return False
            # 同步写 heartbeat Hash（保留运维 dashboard 兼容）。
            await self._write_legacy_heartbeat_hash(heartbeat, worker_id)
            return True
        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.exception(f"发送心跳失败: worker_id={worker_id}")
            return False

    def _heartbeat_to_metrics_dict(self, heartbeat: HeartbeatMessage) -> dict:
        """``HeartbeatMessage`` → ``LeaseStore.grant`` 所需的 metrics dict。"""
        return heartbeat_metrics_dict(heartbeat)

    async def _write_legacy_heartbeat_hash(
        self,
        heartbeat: HeartbeatMessage,
        worker_id: str,
    ) -> None:
        """Keep the legacy operations dashboard projection in sync."""
        await write_legacy_heartbeat_hash(self, heartbeat, worker_id)

    async def poll_control(self, timeout: float = 5.0) -> ControlMessage | None:
        """拉取控制消息

        TODO(P3): Direct 模式的 control Stream 暂时保留 dict wire format。
        Gateway 模式已切到 ``ControlService.WatchControl`` server-stream + 类型化
        ``ControlEvent``（见 gateway/transport.py）。Direct 没有 gRPC，
        我们在 P3 让 Master 直接写 typed dict（``ControlEvent`` payload 的扁平
        投影）到这里。当前契约测试 ``test_poll_control_*`` 按 dict 字段断言。
        """
        if not self._redis or not self._running or not self._worker_id:
            return None

        try:
            await self._require_current_generation()
            pending = await self._control_recovery.poll(self._redis, self._control_consumer_name)
            if pending is not None:
                return await self._decode_control_delivery(*pending)
            stream_key = self._keys.control_stream(self._worker_id)
            results = await self._redis.xreadgroup(
                groupname=self._control_group,
                consumername=self._control_consumer_name,
                streams={stream_key: ">"},
                count=1,
                block=max(1, int(timeout * MILLISECONDS_PER_SECOND)),
            )
            if not results:
                return None

            stream_key, messages = results[0]
            if not messages:
                return None

            msg_id, data = messages[0]
            return await self._decode_control_delivery(stream_key, msg_id, data)
        except Exception:
            self._control_recovery.reset()
            logger.exception("拉取控制消息失败")
            raise

    async def ack_control(self, receipt: str) -> bool:
        """确认控制消息"""
        if not self._redis or not self._running:
            return False

        try:
            await self._require_current_generation()
            source = self._control_source(receipt)
            await self._ack_control_source(source)
            return True
        except Exception as exc:
            raise_if_generation_lost(exc)
            self._control_recovery.reset()
            logger.exception("确认控制消息失败")
            return False

    async def send_control_result(
        self,
        request_id: str,
        reply_stream: str,
        success: bool,
        *,
        receipt: str = "",
        data: Any = None,
        error: str = "",
    ) -> bool:
        """回传控制结果

        TODO(P3): 新 ``ControlService.AckControl`` 已经携带 ``success`` /
        ``error`` 字段。Direct 模式没有 gRPC 通道，这里继续走 reply Stream
        （JSON dict）。Gateway 模式见 gateway/transport.py 的等价实现。
        契约测试 ``test_send_control_result_*`` 按 dict 字段断言。
        """
        if not self._redis or not self._running:
            return False

        source: ControlSource | None = None
        try:
            await self._require_current_generation()
            if not receipt:
                raise ValueError("Direct 运行时控制结果缺少原控制事件 receipt")
            source = self._control_source(receipt)
            expected_reply = f"{self._keys.namespace}:control:reply:{request_id}"
            result = RuntimeControlResult(
                request_id=request_id,
                reply_stream=reply_stream,
                success=success,
                data=data,
                error=error,
            )
            await settle_runtime_control_result(
                self._redis,
                source=source,
                result=result,
                expected_reply_stream=expected_reply,
                worker_id=self._worker_id or "",
                lease_id=self._lease_id,
                lease_key=self._lease_store.lease_key(self._worker_id or ""),
                namespace=self._keys.namespace,
            )
            await self._require_current_generation()
            acknowledged = await ack_owned_stream_entry(
                self._redis,
                stream_key=source.channel.stream_key,
                group=source.channel.group,
                message_id=source.message_id,
                consumer_name=source.consumer_name,
            )
            if int(acknowledged or 0) not in (0, 1):
                raise RuntimeError("Direct 运行时控制 ACK 响应无效")
            await self._trim_committed_control(source.channel)
            return True
        except SettlementEvidenceError:
            logger.exception("Direct 运行时控制 settlement 证据损坏，隔离原控制事件")
            if source is None:
                return False
            try:
                await self._ack_quarantined_control_source(source)
            except Exception as exc:
                raise_if_generation_lost(exc)
                logger.exception("Direct 运行时控制损坏事件隔离失败")
                return False
            return True
        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.exception("回传控制结果失败")
            return False

    def _build_control_channels(self) -> tuple[ControlChannel, ...]:
        worker_stream = self._keys.control_stream(self._worker_id or "worker")
        return (ControlChannel(worker_stream, self._control_group),)

    def _build_control_message(self, stream_key: str, msg_id: str, data: dict[Any, Any]) -> ControlMessage:
        decoded = self._decode_data(data)
        payload: dict[str, Any] = decoded
        if decoded.get("control_type") == "config_update":
            config_payload = decoded.get("config")
            if not isinstance(config_payload, dict):
                raise ValueError("Direct config_update config 必须是 object")
            payload = config_payload
        return ControlMessage(
            control_type=decoded.get("control_type", ""),
            task_id=decoded.get("task_id", ""),
            run_id=decoded.get("run_id", ""),
            reason=decoded.get("reason", ""),
            payload=payload,
            receipt=self._encode_receipt(stream_key, msg_id),
        )

    # 坏控制帧日志里的单字段截断长度，避免刷爆日志。
    _BAD_CONTROL_FIELD_LOG_CHARS = 512

    async def _decode_control_delivery(
        self,
        stream_key: str,
        message_id: str,
        data: dict[Any, Any],
    ) -> ControlMessage | None:
        await self._require_current_generation()
        self._control_in_flight.add((stream_key, message_id))
        try:
            message = self._build_control_message(stream_key, message_id, data)
        except self._BAD_FRAME_ERRORS as exc:
            # P1-DR-02（控制面）：坏帧只 reset recovery 会被 pending
            # recovery 无限重投，堵塞整个控制面（cancel 等指令全部延迟）。
            # 复用 settlement 证据损坏的隔离出口：记录原始帧（截断）后
            # owned-ACK 释放 PEL。ACK 失败会冒泡，消息保留重试。
            raw_sample = {
                self._settlement_text(k)[: self._BAD_CONTROL_FIELD_LOG_CHARS]: self._settlement_text(v)[
                    : self._BAD_CONTROL_FIELD_LOG_CHARS
                ]
                for k, v in (data or {}).items()
            }
            logger.error(
                "坏控制帧隔离(ACK 释放 PEL): stream={} msg={} error={}: {} raw={}",
                stream_key,
                message_id,
                type(exc).__name__,
                exc,
                raw_sample,
            )
            source = self._control_source(self._encode_receipt(stream_key, message_id))
            await self._ack_quarantined_control_source(source)
            return None
        if message.control_type != "runtime_manage":
            return message
        source = self._control_source(self._encode_receipt(stream_key, message_id))
        try:
            recovered = await recover_runtime_control_settlement(
                self._redis,
                source=source,
                worker_id=self._worker_id or "",
                lease_id=self._lease_id,
                lease_key=self._lease_store.lease_key(self._worker_id or ""),
                namespace=self._keys.namespace,
            )
        except (SettlementEvidenceError, ValueError):
            logger.exception("Direct 运行时控制来源或 settlement 证据损坏，隔离原控制事件")
            await self._ack_quarantined_control_source(source)
            return None
        if not recovered:
            try:
                validate_runtime_request(
                    message.payload,
                    self._worker_id or "",
                    namespace=self._keys.namespace,
                )
            except ValueError as exc:
                logger.error(
                    "畸形运行时控制事件隔离: stream={} msg={} error={}",
                    stream_key,
                    message_id,
                    exc,
                )
                await self._ack_quarantined_control_source(source)
                return None
            return message
        await self._ack_control_source(source)
        return None

    async def _owned_control_ack(self, source: ControlSource, purpose: str) -> int:
        if self._redis is None:  # owned-ACK control 条目；成功后解除在途登记
            raise RuntimeError(f"Direct Redis 未连接，无法{purpose}控制事件")
        await self._require_current_generation()
        acknowledged = await ack_owned_stream_entry(
            self._redis,
            stream_key=source.channel.stream_key,
            group=source.channel.group,
            message_id=source.message_id,
            consumer_name=source.consumer_name,
        )
        if acknowledged == 1:
            self._control_in_flight.discard((source.channel.stream_key, source.message_id))
        return acknowledged

    async def _ack_control_source(self, source: ControlSource) -> None:
        acknowledged = await self._owned_control_ack(source, "确认")
        if acknowledged != 1:
            raise RuntimeError(describe_ack_failure(acknowledged, source))
        await self._trim_committed_control(source.channel)

    async def _ack_quarantined_control_source(self, source: ControlSource) -> None:
        if await self._owned_control_ack(source, "隔离") != 1:
            raise RuntimeError("Direct 损坏控制事件 ACK 失败")

    def _control_source(self, receipt: str) -> ControlSource:
        stream_key, message_id = self._decode_receipt(receipt)
        channels = {channel.stream_key: channel for channel in self._control_channels}
        if not message_id or stream_key not in channels:
            raise ValueError("Direct 运行时控制 receipt 不属于当前 Worker control stream")
        return ControlSource(channels[stream_key], message_id, self._control_consumer_name)

    async def _trim_committed_control(self, channel: ControlChannel) -> None:
        try:
            await trim_acknowledged_stream(self._redis, channel.stream_key, channel.group)
        except Exception:
            logger.exception("控制结果 ACK 后裁剪失败: stream={}", channel.stream_key)

    def get_status(self) -> dict[str, Any]:
        """获取传输层状态（凭据打码，不得回显 ACL 密码）。"""
        return {
            "mode": self.mode.value,
            "state": self._state.value,
            "running": self._running,
            "redis_url": self._redacted_redis_url(),
            "connected": self._redis is not None,
        }

    def _redacted_redis_url(self) -> str:
        from urllib.parse import urlsplit, urlunsplit

        try:
            parts = urlsplit(self._redis_url)
        except ValueError:
            return "<unparseable redis url>"
        if not parts.password and not parts.username:
            return self._redis_url
        host = parts.hostname or ""
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        username = parts.username or ""
        netloc = f"{username}:***@{host}" if username else f":***@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    # ==================== 爬虫数据操作 ====================
    async def report_spider_data(
        self,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> bool:
        """
        上报爬虫数据到 Redis

        Args:
            run_id: 运行 ID
            items: 数据条目列表（每条需包含 to_redis_dict 方法或为 dict）
        Returns:
            是否成功
        """
        if not self._running or self._direct_control is None:
            return False

        if not items:
            return True

        try:
            await self._require_current_generation()
            project_id, payloads = normalize_spider_items(run_id, items)
            written = await self._direct_control.report_spider_items(
                lease_id=self._lease_id,
                run_id=run_id,
                project_id=project_id,
                items=payloads,
            )
            await self._require_current_generation()
            return written
        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.exception("上报爬虫数据失败")
            return False

    async def update_spider_meta(
        self,
        run_id: str,
        meta: dict[str, Any],
    ) -> bool:
        """
        更新爬虫元数据

        Args:
            run_id: 运行 ID
            meta: 元数据字典（或 SpiderMeta 对象）
        Returns:
            是否成功
        """
        if not self._running or self._direct_control is None:
            return False

        try:
            await self._require_current_generation()
            # 支持 SpiderMeta 对象或普通 dict
            if hasattr(meta, "to_redis_dict"):
                data = meta.to_redis_dict()
            else:
                data = {k: str(v) if not isinstance(v, str) else v for k, v in meta.items()}

            project_id = str(data.get("project_id") or "")
            if not project_id:
                raise ValueError("SpiderData meta 缺少 project_id")
            written = await self._direct_control.update_spider_meta(
                lease_id=self._lease_id,
                run_id=run_id,
                project_id=project_id,
                meta=data,
            )
            await self._require_current_generation()
            return written
        except Exception as exc:
            raise_if_generation_lost(exc)
            logger.exception("更新爬虫元数据失败")
            return False

    def get_spider_data_reporter(
        self,
        run_id: str,
        project_id: str,
        spider_name: str,
        **kwargs: Any,
    ):
        """
        获取爬虫数据上报器

        Args:
            run_id: 运行 ID
            project_id: 项目 ID
            spider_name: 爬虫名称
            **kwargs: 其他配置

        Returns:
            RedisDataReporter 实例
        """
        raise RuntimeError("Direct Redis Spider reporter 已停用；请通过 Worker transport 的可信控制面上报")

    async def reconnect(self) -> bool:
        """重连 Redis。

        W3: start/stop/reconnect 共用 _reconnect_lock，避免并发调用者交错建出
        多套 Redis / PendingTaskReclaimer / DeferredTaskRecovery。锁内调用不再
        重复取锁的 _start_locked() 与 _stop_locked()，最终 shutdown 必须胜出。
        """
        stale = self._redis
        async with self._reconnect_lock:
            if not self._running or self._redis is None:
                return False
            # 去重：另一路并发重连已把 client 换新并恢复运行 —— 直接复用，
            # 不再把刚建好的连接拆掉重建。若 client 仍是本调用进入前观察到的
            # 那一个（即错误确实发生在当前 client 上），才真正重连。
            if self._running and self._redis is not None and self._redis is not stale:
                return True
            try:
                await self._stop_locked(close_control=False)
                return await self._start_locked()
            except Exception:
                return False

    def _encode_receipt(self, stream_key: str, msg_id: str) -> str:
        return f"{stream_key}|{msg_id}"

    def _decode_receipt(self, receipt: str) -> tuple[str, str]:
        if "|" not in receipt:
            return "", ""
        stream_key, msg_id = receipt.split("|", 1)
        return stream_key, msg_id

    def _decode_data(self, data: dict[str, Any]) -> dict[str, Any]:
        decoded = decode_stream_payload(data)

        # 处理布尔值字段
        for field in ("is_compressed",):
            if field in decoded and isinstance(decoded[field], str):
                val = decoded[field].lower()
                if val == "true":
                    decoded[field] = True
                elif val == "false":
                    decoded[field] = False
                else:
                    decoded[field] = None

        return decoded

    def _decode_source_bundle(self, decoded: dict[str, Any]) -> SourceBundle | None:
        uri = str(decoded.get("source_bundle_uri") or "")
        digest = str(decoded.get("source_bundle_sha256") or "")
        if not uri and str(decoded.get("project_type") or "").lower() == "rule":
            # rule 任务没有源码 bundle：master 派发时按 project_type 跳过
            # bundle 构建，source_bundle_uri 恒为空。返回 None，
            # engine._build_payload 对 rule 允许 None bundle；非 rule 任务
            # 缺 URI 仍走下面的 fail-fast 校验，安全语义不变。
            # （与 gateway codecs._decode_source_bundle 的 rule 分流保持一致）
            return None
        match = re.fullmatch(r"pgartifact://([0-9a-f]{64})", uri)
        if match is None:
            raise ValueError("source_bundle_uri 必须是 pgartifact://<sha256>")
        if digest != match.group(1):
            raise ValueError("source_bundle_sha256 与 URI 摘要不一致")
        return SourceBundle(
            uri=uri,
            sha256=digest,
            size=int(decoded.get("source_bundle_size", 0) or 0),
            transfer_method=str(decoded.get("transfer_method") or "source_bundle"),
            entry_point=str(decoded.get("entry_point") or ""),
            resolved_revision=str(decoded.get("resolved_revision") or ""),
            source_subdir=str(decoded.get("source_subdir") or ""),
        )
