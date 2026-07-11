"""
Redis 传输层实现（Direct 模式）

内网 Worker 直连 Redis Streams，低延迟。

P1b 改造：
- ``report_result`` 写入 ``task:result`` Stream 时使用 Proto bytes
  （``data_pb2.TaskStatus`` 序列化到 ``PROTO_FIELD``），
  与 Master ``result_loop`` 的 ``ProtoCodec`` 对齐。
- ``send_log`` / ``send_log_batch`` 写入 log Stream 时使用 Proto bytes
  （``data_pb2.LogBatch``），与 Master ``log_ingest_loop`` 对齐。
- ``send_log_chunk`` / ``send_heartbeat`` / ``poll_control`` /
  ``send_control_result`` 暂时保留原有 dict/JSON wire format
  （契约测试 + Gateway/Master 配合到位前的过渡），P3 收尾。

Requirements: 5.3, 7.2, 11.3
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from redis.asyncio import Redis

from antcode_contracts import data_pb2
from antcode_contracts.transcode import (
    datetime_to_proto_timestamp,
    encode_task_status,
    log_type_str_to_proto,
)
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream
from antcode_core.observability.tracing import inject_trace
from loguru import logger
from redis.exceptions import ConnectionError, TimeoutError

from antcode_worker.domain.models import SourceBundle
from antcode_worker.transport.base import (
    ControlMessage,
    HeartbeatMessage,
    LogMessage,
    ServerConfig,
    TaskMessage,
    TaskResult,
    TransportBase,
    TransportMode,
    WorkerState,
)
from antcode_worker.transport.redis.keys import RedisKeys
from antcode_worker.transport.redis.reclaim import PendingTaskReclaimer, ensure_consumer_group

# P1: Status / LogType / Timestamp 转换全部走 ``antcode_contracts.transcode``
# （原来这里有本地 ``_STATUS_STR_TO_PROTO`` / ``_log_type_str_to_proto`` /
# ``_datetime_to_proto_timestamp`` 三份拷贝，与 gateway/codecs.py 重复）。


class RedisTransport(TransportBase):
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
        namespace: str | None = None,
        consumer_group: str | None = None,
        control_group: str | None = None,
    ):
        if not redis_url:
            raise ValueError("redis_url 必须显式配置")
        super().__init__(config)
        self._redis_url = redis_url
        self._redis: Redis | None = None
        self._worker_id = worker_id
        resolved_namespace = namespace or getattr(self._config, "redis_namespace", None)
        self._keys = RedisKeys(namespace=resolved_namespace)
        self._consumer_group = consumer_group or self._keys.consumer_group_name()
        self._consumer_name = self._keys.consumer_name(worker_id) if worker_id else "worker"
        self._control_group = control_group or self._keys.consumer_group_name("control")
        self._global_control_group = f"{self._control_group}:{worker_id or 'worker'}"
        self._reclaimer: PendingTaskReclaimer | None = None
        self._receipt_cache: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._poll_error_count = 0
        self._poll_backoff_until = 0.0
        # R1-P0-4: 被 reclaimer 认领回来的消息塞在这里，poll_task 会优先消费
        # 队列元素形如 (msg_id, decoded_data)
        self._reclaimed_queue: asyncio.Queue = asyncio.Queue()
        # P3：Direct 模式下 Worker 直连 Redis，自带 LeaseStore（共享 redis_client）。
        self._lease_store: Any = None
        self._lease_id: str = ""

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
                await ensure_consumer_group(
                    redis,
                    self._keys.control_global_stream(),
                    self._global_control_group,
                )

                # 启动 pending 回收器
                # R1-P0-4 (审查报告)：认领到的消息通过 on_reclaimed 回调塞进
                # 本地 _reclaimed_queue，poll_task 优先消费该队列 → 消息回到
                # engine 执行。原实现丢弃 _do_reclaim 返回值，认领等于空转。
                self._reclaimer = PendingTaskReclaimer(
                    redis_client=self._redis,
                    worker_id=self._worker_id,
                    keys=self._keys,
                    on_reclaimed=self._enqueue_reclaimed,
                )
                await self._reclaimer.start()

                # P3：构造本地 LeaseStore（共享 redis_client + namespace）。
                # Direct 模式没有 Gateway 接管 lease，Worker 自己 grant 即可。
                try:
                    from antcode_core.application.services.lease_service import (
                        LeasePolicy,
                        LeaseStore,
                    )

                    ns = getattr(self._keys, "namespace", None) or "antcode"
                    self._lease_store = LeaseStore(
                        redis_client=self._redis,
                        namespace=ns,
                        policy=LeasePolicy(),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(f"Direct 模式构造 LeaseStore 失败: {exc}")

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
                if self._redis:
                    await self._redis.aclose()
                    self._redis = None
                if not self._is_connection_error(e) or attempt >= max_attempts:
                    logger.exception("Redis 连接失败")
                    return False
                logger.warning(f"Redis 连接失败，{delay:.1f}s 后重试 ({attempt}/{max_attempts})")
                await asyncio.sleep(delay)
                delay = min(2.0, delay * 2)

        return False

    async def stop(self, grace_period: float = 5.0) -> None:
        """停止 Redis 连接"""
        if not self._running:
            return

        self._running = False

        if self._reclaimer:
            await self._reclaimer.stop()
            self._reclaimer = None

        if self._redis:
            await self._redis.aclose()
            self._redis = None

        await self._set_state(WorkerState.OFFLINE)
        logger.info("Redis 传输层已停止")

    async def deregister(self, reason: str = "shutdown") -> None:
        """T7-B3b (P1-6): Direct 模式主动 revoke lease + 删心跳。

        让 master 立即判定 worker 离线，不再等 lease TTL（30s）。
        Redis 已经 close 时降级为 no-op。
        """
        if not self._worker_id or not self._redis:
            return
        try:
            if self._lease_store is not None:
                await self._lease_store.revoke(self._worker_id, reason=reason)
        except Exception as exc:
            logger.warning(f"deregister revoke lease 失败: {exc}")
        try:
            from antcode_core.infrastructure.redis import worker_heartbeat_key

            await self._redis.delete(worker_heartbeat_key(self._worker_id))
        except Exception as exc:
            logger.debug(f"deregister 清心跳 key 失败（可忽略）: {exc}")
        logger.info(f"worker 主动 deregister 完成 (reason={reason})")

    async def _enqueue_reclaimed(self, msg_id: str, data: dict[str, str]) -> None:
        """R1-P0-4: reclaimer 的 on_reclaimed 回调。

        认领到的消息塞进本地队列；poll_task 会优先消费。data 已经是 Redis
        原始 field map（bytes 或 str），走 _decode_data 与正常 poll 一致。
        """
        await self._reclaimed_queue.put((msg_id, data))

    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """
        从 Redis Streams 拉取任务

        使用 XREADGROUP 从 ready queue 读取任务。R1-P0-4: 优先消费本地
        _reclaimed_queue（reclaimer 认领的旧 PEL 消息）。
        """
        if not self._redis or not self._running:
            return None

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
                stream_name = stream_key
                decoded = self._decode_data(data)
                receipt = self._encode_receipt(stream_name, msg_id)
                # 走原来的构造分支
                self._poll_error_count = 0
                self._poll_backoff_until = 0.0
                # fallthrough 到下面构造 TaskMessage 用 decoded
                # 但为了不重复代码，跳到公共构造点：复制一份
                return self._build_task_message(stream_name, msg_id, decoded, receipt)

            now = time.monotonic()
            if self._poll_backoff_until > now:
                await asyncio.sleep(self._poll_backoff_until - now)

            result = await self._redis.xreadgroup(
                groupname=self._consumer_group,
                consumername=self._consumer_name,
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
            decoded = self._decode_data(data)
            receipt = self._encode_receipt(stream_name, msg_id)
            # 构造 TaskMessage 走公共入口（含 SourceBundle 契约、traceparent 挂载）
            return self._build_task_message(stream_name, msg_id, decoded, receipt)

        except Exception:
            self._poll_error_count += 1
            delay = min(30.0, 0.5 * (2 ** (self._poll_error_count - 1)))
            self._poll_backoff_until = time.monotonic() + delay
            logger.exception("拉取任务失败")
            logger.warning(f"拉取任务退避 {delay:.1f}s (连续失败 {self._poll_error_count} 次)")
            if self._poll_error_count % 3 == 0:
                await self.reconnect()
            raise

    def _build_task_message(
        self,
        stream_name: str,
        msg_id: str,
        decoded: dict[str, Any],
        receipt: str,
    ) -> TaskMessage:
        """R1-P0-4: 抽出的 TaskMessage 构造逻辑，poll_task 与 reclaimer 复用。"""
        # source_bundle 与 poll_task 主流程保持一致
        if "project_path" in decoded:
            raise ValueError("project_path 已废弃，任务必须使用 source_bundle")
        source_bundle = self._decode_source_bundle(decoded)

        task_msg = TaskMessage(
            task_id=decoded.get("task_id", ""),
            project_id=decoded.get("project_id", ""),
            project_type=decoded.get("project_type", "code"),
            priority=int(decoded.get("priority", 0) or 0),
            params=decoded.get("params", {}) or {},
            environment=decoded.get("environment", {}) or {},
            timeout=int(decoded.get("timeout", 3600) or 3600),
            source_bundle=source_bundle,
            source_subdir=decoded.get("source_subdir", "") or "",
            entry_point=decoded.get("entry_point", "") or "",
            run_id=decoded.get("run_id", "") or "",
            receipt=receipt,
        )

        traceparent = decoded.get("trace_parent") or decoded.get("traceparent") or ""
        if traceparent:
            task_msg.traceparent = traceparent  # type: ignore[attr-defined]

        self._receipt_cache[receipt] = (stream_name, msg_id, decoded)
        return task_msg

    async def ack_task(self, task_id: str, accepted: bool, reason: str = "") -> bool:
        """确认任务"""
        if not self._redis or not self._running:
            return False

        try:
            if not accepted:
                return await self.requeue_task(task_id, reason=reason)

            stream_key, msg_id = self._decode_receipt(task_id)
            if not stream_key:
                return False

            acknowledged = await self._run_with_reconnect(
                "确认任务",
                lambda: self._redis.xack(stream_key, self._consumer_group, msg_id),
            )
            if int(acknowledged or 0) != 1:
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

        except Exception:
            logger.exception("确认任务失败")
            return False

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
            result_key = self._keys.task_result_stream()
            ts_msg = encode_task_status(
                run_id=result.run_id or "",
                task_id=result.task_id or "",
                worker_id=self._worker_id or "",
                status=result.status,
                exit_code=int(result.exit_code or 0),
                error_message=result.error_message or "",
                started_at=result.started_at,
                finished_at=result.finished_at,
                duration_ms=int(result.duration_ms or 0),
                data=result.data,
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
            return True

        except Exception:
            logger.exception("上报结果失败")
            return False

    # U6: requeue 病毒消息阈值 + 死信 stream。
    MAX_REQUEUE_COUNT = 5
    DEAD_LETTER_STREAM_SUFFIX = "task:dead_letter"

    async def requeue_task(self, receipt: str, reason: str = "") -> bool:
        """重新入队任务

        U6 修复点:
        - 之前的实现没有 requeue 次数上限,某条"恶意 / 总是失败"的消息
          会被无限重投,把 Worker 卡死在一条任务上(病毒消息)。这里加
          ``MAX_REQUEUE_COUNT`` 上限,超过后写入 ``task:dead_letter``
          Stream 并直接 ACK,避免死循环。
        - 之前的实现把 cached dict 直接 ``xadd`` —— 但缓存里的 ``params``
          / ``environment`` 已经被 ``_decode_data`` ``json.loads`` 成 dict,
          ``xadd`` 不接受 nested dict 值(redis-py 会按 ``str(dict)`` stringify,
          下一次 Worker poll 时再 ``json.loads`` 直接抛异常)。这里对
          这几个字段在 requeue 写回前重新 ``json.dumps``。
        """
        if not self._redis or not self._running:
            return False

        try:
            cached = self._receipt_cache.get(receipt)
            if not cached:
                return False

            stream_key, msg_id, data = cached
            data = dict(data)

            # ---- 1) 计数 + 死信检查 ----
            try:
                current_count = int(data.get("requeue_count", "0") or "0")
            except (TypeError, ValueError):
                current_count = 0
            requeue_count = current_count + 1

            if requeue_count > self.MAX_REQUEUE_COUNT:
                # 写死信 + ack 原消息。死信 stream 用 namespace 拼接,与
                # 现有 stream key naming 风格一致。
                ns = getattr(self._keys, "namespace", None) or "antcode"
                dead_letter_key = f"{ns}:{self.DEAD_LETTER_STREAM_SUFFIX}"
                dead_payload = self._serialize_for_xadd(data)
                dead_payload["requeue_count"] = str(requeue_count)
                dead_payload["requeue_reason"] = reason or ""
                dead_payload["dead_at"] = datetime.now().isoformat()
                await self._run_with_reconnect(
                    "写入死信 stream",
                    lambda: self._redis.xadd(
                        dead_letter_key,
                        cast(dict[Any, Any], dead_payload),
                        maxlen=10000,
                        approximate=True,
                    ),
                )
                await self._run_with_reconnect(
                    "死信 ack",
                    lambda: self._redis.xack(stream_key, self._consumer_group, msg_id),
                )
                self._receipt_cache.pop(receipt, None)
                logger.warning(
                    f"消息进入死信(超过 {self.MAX_REQUEUE_COUNT} 次 requeue): "
                    f"receipt={receipt} count={requeue_count} reason={reason}"
                )
                return True

            # ---- 2) 正常 requeue:把 nested dict 字段重新 JSON 编码 ----
            requeue_payload = self._serialize_for_xadd(data)
            requeue_payload["requeue_count"] = str(requeue_count)
            requeue_payload["requeue_reason"] = reason or ""
            requeue_payload["requeue_at"] = datetime.now().isoformat()

            await self._run_with_reconnect(
                "任务重新入队",
                lambda: self._redis.xadd(
                    stream_key,
                    cast(dict[Any, Any], requeue_payload),
                ),
            )
            await self._run_with_reconnect(
                "重新入队确认",
                lambda: self._redis.xack(stream_key, self._consumer_group, msg_id),
            )
            self._receipt_cache.pop(receipt, None)
            return True
        except Exception:
            logger.exception("重新入队失败")
            return False

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

    def _build_log_entry_proto(self, log: LogMessage):
        """``LogMessage`` → ``data_pb2.LogEntry``"""
        entry = data_pb2.LogEntry(
            run_id=log.run_id or "",
            log_type=log_type_str_to_proto(log.log_type),
            content=log.content or "",
            sequence=int(log.sequence or 0),
        )
        ts = datetime_to_proto_timestamp(log.timestamp or datetime.now())
        if ts is not None:
            entry.timestamp.CopyFrom(ts)
        return entry

    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        """发送批量日志

        Follow-up #1 (post-review): 不再按 run_id 写到 per-run stream
        ``antcode:log:stream:{run_id}``,改为统一写到全局 ingest stream
        ``antcode:log:ingest``。这是和 Master ``log_ingest_loop`` 默认订阅 key
        对齐的关键 —— 此前两边 key 永不相交,Direct 模式日志无法落库。

        每个 ``run_id`` 仍单独打包成一条 ``LogBatch`` Proto(保持 batch 内
        单一 run_id 语义),多个 run_id 的 batch 在同一 pipeline 内全部
        XADD 到同一个 ingest stream。stream 由 MAXLEN 维持大小,不设
        EXPIRE(共享 stream 永久存在,Master 通过 XACK 维护游标)。
        """
        if not self._redis or not self._running:
            return False

        if not logs:
            return True

        try:
            redis = self._redis
            if redis is None:
                return False
            maxlen = self._keys.config.stream_max_len

            # 按 run_id 分组打包,每个 run_id 一条 Proto LogBatch
            batches: dict[str, list[LogMessage]] = {}
            for log in logs:
                batches.setdefault(log.run_id or "", []).append(log)

            # Follow-up #1: 统一 ingest stream,不再按 run_id 分 stream
            ingest_key = self._keys.log_ingest_stream()

            async def _write_batches():
                pipe = redis.pipeline(transaction=False)
                for run_id, run_logs in batches.items():
                    batch_msg = data_pb2.LogBatch(
                        worker_id=self._worker_id or "",
                    )
                    for log in run_logs:
                        batch_msg.entries.append(self._build_log_entry_proto(log))
                    # P5.4: 把当前 trace 注入到每个 LogBatch,Master 端
                    # ingester 可据此把同一 run 的日志按 trace_id 关联到调用链。
                    inject_trace(batch_msg)
                    payload_bytes = batch_msg.SerializeToString()
                    if maxlen > 0:
                        pipe.xadd(
                            ingest_key,
                            {PROTO_FIELD: payload_bytes},
                            maxlen=maxlen,
                            approximate=self._keys.config.stream_approx_max_len,
                        )
                    else:
                        pipe.xadd(ingest_key, {PROTO_FIELD: payload_bytes})

                return await pipe.execute(raise_on_error=False)

            results = await self._run_with_reconnect("发送批量日志", _write_batches)
            if len(results) != len(batches):
                logger.error("发送批量日志返回数量不匹配")
                return False
            for result in results:
                if isinstance(result, Exception) or not result:
                    logger.error(f"发送批量日志失败: {result}")
                    return False
            return True
        except Exception:
            logger.exception("发送批量日志失败")
            return False

    async def send_log_chunk(
        self,
        run_id: str,
        log_type: str,
        data: bytes,
        offset: int,
        is_final: bool = False,
    ) -> bool:
        """发送日志分片 — 已弃用，no-op。

        P2: 之前会把 ``[CHUNK] offset=N <base64>`` 拼到 LogBatch content 里塞
        Stream，违反 Stream 单字段语义且会撑大日志体。大块二进制现在应该走
        ``source_bundle`` / ``pgartifact`` 通道，本方法保留签名只为兼容
        旧调用方，不再实际写入 Stream。

        Returns:
            恒为 ``True``（兼容调用方）。
        """
        size = len(data) if data else 0
        logger.warning(
            "send_log_chunk 已弃用,大块二进制应走 source_bundle/pgartifact: "
            f"run_id={run_id} log_type={log_type} offset={offset} "
            f"size={size} is_final={is_final}"
        )
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
        except Exception:
            logger.exception(f"发送心跳失败: worker_id={worker_id}")
            return False

    def _heartbeat_to_metrics_dict(self, heartbeat: HeartbeatMessage) -> dict:
        """``HeartbeatMessage`` → ``LeaseStore.grant`` 所需的 metrics dict。"""
        metrics = getattr(heartbeat, "metrics", None)
        if metrics is not None:
            return {
                "cpu": getattr(metrics, "cpu", 0.0),
                "memory": getattr(metrics, "memory", 0.0),
                "disk": getattr(metrics, "disk", 0.0),
                "running_tasks": getattr(metrics, "running_tasks", 0),
                "max_concurrent_tasks": getattr(metrics, "max_concurrent_tasks", 5),
            }
        return {
            "cpu": getattr(heartbeat, "cpu_percent", 0.0),
            "memory": getattr(heartbeat, "memory_percent", 0.0),
            "disk": getattr(heartbeat, "disk_percent", 0.0),
            "running_tasks": getattr(heartbeat, "running_tasks", 0),
            "max_concurrent_tasks": getattr(heartbeat, "max_concurrent_tasks", 5),
        }

    async def _write_legacy_heartbeat_hash(
        self,
        heartbeat: HeartbeatMessage,
        worker_id: str,
    ) -> None:
        """过渡期：保留 ``heartbeat:{worker_id}`` Hash 视图。

        新判活信号是 lease，本 Hash 只供 web_api / 运维 dashboard 读最近
        一次上报指标。Hash 过期时间仍按 ``heartbeat_interval * 3`` 兜底，
        合理范围内。
        """
        if not self._redis:
            return

        status = getattr(heartbeat, "status", "online")
        timestamp = getattr(heartbeat, "timestamp", None) or datetime.now()
        metrics = getattr(heartbeat, "metrics", None)
        if metrics is not None:
            cpu_percent = getattr(metrics, "cpu", 0.0)
            memory_percent = getattr(metrics, "memory", 0.0)
            disk_percent = getattr(metrics, "disk", 0.0)
            running_tasks = getattr(metrics, "running_tasks", 0)
            max_concurrent_tasks = getattr(metrics, "max_concurrent_tasks", 5)
        else:
            cpu_percent = getattr(heartbeat, "cpu_percent", 0.0)
            memory_percent = getattr(heartbeat, "memory_percent", 0.0)
            disk_percent = getattr(heartbeat, "disk_percent", 0.0)
            running_tasks = getattr(heartbeat, "running_tasks", 0)
            max_concurrent_tasks = getattr(heartbeat, "max_concurrent_tasks", 5)

        name = getattr(heartbeat, "name", None)
        host = getattr(heartbeat, "host", None)
        port = getattr(heartbeat, "port", None)
        region = getattr(heartbeat, "region", None)
        version = getattr(heartbeat, "version", None)
        capabilities = getattr(heartbeat, "capabilities", None)
        os_info = getattr(heartbeat, "os_info", None)
        os_type = getattr(os_info, "os_type", None) if os_info else None
        os_version = getattr(os_info, "os_version", None) if os_info else None
        python_version = getattr(os_info, "python_version", None) if os_info else None
        machine_arch = getattr(os_info, "machine_arch", None) if os_info else None

        hb_key = self._keys.heartbeat_key(worker_id)
        redis = self._redis
        if redis is None:
            raise RuntimeError("Redis transport is not connected")

        async def _write_heartbeat():
            mapping = {
                "status": status,
                "cpu_percent": str(cpu_percent),
                "memory_percent": str(memory_percent),
                "disk_percent": str(disk_percent),
                "running_tasks": str(running_tasks),
                "max_concurrent_tasks": str(max_concurrent_tasks),
                "timestamp": timestamp.isoformat(),
            }
            if name:
                mapping["name"] = str(name)
            if host:
                mapping["host"] = str(host)
            if port:
                mapping["port"] = str(port)
            if region:
                mapping["region"] = str(region)
            if version:
                mapping["version"] = str(version)
            if os_type:
                mapping["os_type"] = str(os_type)
            if os_version:
                mapping["os_version"] = str(os_version)
            if python_version:
                mapping["python_version"] = str(python_version)
            if machine_arch:
                mapping["machine_arch"] = str(machine_arch)
            if capabilities:
                try:
                    mapping["capabilities"] = json.dumps(capabilities, ensure_ascii=False)
                except Exception:
                    pass

            pipe = redis.pipeline(transaction=False)
            pipe.hset(hb_key, mapping=mapping)
            pipe.expire(hb_key, self._config.heartbeat_interval * 3)
            await pipe.execute()

        await self._run_with_reconnect("写心跳 Hash", _write_heartbeat)

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None = None,
    ) -> tuple[str, int, int, bool]:
        """Direct 模式 lease 续租：直接走本地 ``LeaseStore.grant``。

        Returns:
            ``(new_lease_id, expires_at_ms, renew_after_ms, revoked)``。
            Direct 模式没有外部 Master 主动撤销链路，``revoked`` 恒为 False
            （Worker 把 ``lease_renew`` 拿到的 lease_id 作为下一次续租入参）。
        """
        if not self._lease_store or not self._worker_id:
            return ("", 0, 0, False)

        lease = await self._lease_store.grant(
            worker_id=self._worker_id,
            current_lease_id=current_lease_id or "",
            metrics=metrics,
        )
        self._lease_id = lease.lease_id
        return (
            lease.lease_id,
            lease.expires_at_ms,
            self._lease_store.policy.renew_after_ms,
            False,
        )

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
            per_worker_stream = self._keys.control_stream(self._worker_id)
            global_stream = self._keys.control_global_stream()
            half_block_ms = max(1, int(timeout * 500))
            results = await self._redis.xreadgroup(
                groupname=self._control_group,
                consumername=self._consumer_name,
                streams={per_worker_stream: ">"},
                count=1,
                block=half_block_ms,
            )
            if not results:
                results = await self._redis.xreadgroup(
                    groupname=self._global_control_group,
                    consumername=self._consumer_name,
                    streams={global_stream: ">"},
                    count=1,
                    block=half_block_ms,
                )
            if not results:
                return None

            stream_key, messages = results[0]
            if not messages:
                return None

            msg_id, data = messages[0]
            decoded = self._decode_data(data)
            receipt = self._encode_receipt(stream_key, msg_id)

            return ControlMessage(
                control_type=decoded.get("control_type", ""),
                task_id=decoded.get("task_id", ""),
                run_id=decoded.get("run_id", ""),
                reason=decoded.get("reason", ""),
                payload=decoded,
                receipt=receipt,
            )
        except Exception:
            logger.exception("拉取控制消息失败")
            raise

    async def ack_control(self, receipt: str) -> bool:
        """确认控制消息"""
        if not self._redis or not self._running:
            return False

        try:
            stream_key, msg_id = self._decode_receipt(receipt)
            if not stream_key:
                return False
            group = (
                self._global_control_group if stream_key == self._keys.control_global_stream() else self._control_group
            )
            acknowledged = await self._redis.xack(stream_key, group, msg_id)
            if int(acknowledged or 0) != 1:
                return False
            try:
                await trim_acknowledged_stream(self._redis, stream_key, group)
            except Exception:
                logger.exception("控制 ACK 后裁剪失败: stream={}", stream_key)
            return True
        except Exception:
            logger.exception("确认控制消息失败")
            return False

    async def send_control_result(
        self,
        request_id: str,
        reply_stream: str,
        success: bool,
        data: dict | None = None,
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

        try:
            payload = {
                "request_id": request_id,
                "success": str(bool(success)).lower(),
                "data": "" if data is None else json.dumps(data, ensure_ascii=False),
                "error": error or "",
            }
            msg_id = await self._redis.xadd(
                reply_stream,
                cast(dict[Any, Any], payload),
                maxlen=1,
                approximate=True,
            )
            if not msg_id:
                return False
            return bool(await self._redis.expire(reply_stream, 120))
        except Exception:
            logger.exception("回传控制结果失败")
            return False

    def get_status(self) -> dict[str, Any]:
        """获取传输层状态"""
        return {
            "mode": self.mode.value,
            "state": self._state.value,
            "running": self._running,
            "redis_url": self._redis_url,
            "connected": self._redis is not None,
        }

    # ==================== 爬虫数据操作 ====================

    async def report_spider_data(
        self,
        run_id: str,
        items: list[dict[str, Any]],
        ttl_seconds: int = 86400,
        stream_max_len: int = 10000,
    ) -> bool:
        """
        上报爬虫数据到 Redis

        Args:
            run_id: 运行 ID
            items: 数据条目列表（每条需包含 to_redis_dict 方法或为 dict）
            ttl_seconds: 数据过期时间
            stream_max_len: Stream 最大长度

        Returns:
            是否成功
        """
        if not self._redis or not self._running:
            return False

        if not items:
            return True

        try:
            stream_key = self._keys.spider_data_stream(run_id)
            pipe = self._redis.pipeline()

            for item in items:
                # 支持 SpiderDataItem 对象或普通 dict
                if hasattr(item, "to_redis_dict"):
                    data = item.to_redis_dict()
                else:
                    data = {k: str(v) if not isinstance(v, str) else v for k, v in item.items()}

                if stream_max_len > 0:
                    pipe.xadd(stream_key, data, maxlen=stream_max_len, approximate=True)
                else:
                    pipe.xadd(stream_key, data)

            if ttl_seconds > 0:
                pipe.expire(stream_key, ttl_seconds)

            await pipe.execute()
            return True
        except Exception:
            logger.exception("上报爬虫数据失败")
            return False

    async def update_spider_meta(
        self,
        run_id: str,
        meta: dict[str, Any],
        ttl_seconds: int = 86400,
    ) -> bool:
        """
        更新爬虫元数据

        Args:
            run_id: 运行 ID
            meta: 元数据字典（或 SpiderMeta 对象）
            ttl_seconds: 过期时间

        Returns:
            是否成功
        """
        if not self._redis or not self._running:
            return False

        try:
            meta_key = self._keys.spider_meta_key(run_id)

            # 支持 SpiderMeta 对象或普通 dict
            if hasattr(meta, "to_redis_dict"):
                data = meta.to_redis_dict()
            else:
                data = {k: str(v) if not isinstance(v, str) else v for k, v in meta.items()}

            await cast(Awaitable[Any], self._redis.hset(meta_key, mapping=data))

            if ttl_seconds > 0:
                await self._redis.expire(meta_key, ttl_seconds)

            return True
        except Exception:
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
        from antcode_worker.plugins.spider.data import RedisDataReporter

        if self._redis is None:
            raise RuntimeError("Redis transport 尚未启动")
        return RedisDataReporter(
            redis_client=self._redis,
            keys=self._keys,
            run_id=run_id,
            project_id=project_id,
            spider_name=spider_name,
            **kwargs,
        )

    async def reconnect(self) -> bool:
        """重连 Redis"""
        try:
            await self.stop()
            return await self.start()
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
        decoded: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            decoded[key] = value

        for field in ("params", "environment", "data", "payload"):
            if field in decoded and isinstance(decoded[field], str):
                try:
                    decoded[field] = json.loads(decoded[field])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{field} 不是合法 JSON: {exc}") from exc

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
