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
import base64
import contextlib
import json
import time
from datetime import datetime
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from loguru import logger
from redis.exceptions import ConnectionError, TimeoutError

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

# ---------------------------------------------------------------------------
# 字符串状态 → Proto Status enum 映射
#
# Worker 内部仍然按 ``TaskResult.status`` 字符串语义传递；写 Stream 时把
# 字符串映射到 ``data_pb2.Status``，与 Master ``result_loop`` 的反向映射对齐
# （``STATUS_COMPLETED`` → ``completed``，详见 result_loop._proto_status_to_str）。
# ``success`` / ``completed`` / ``done`` 全部归并到 ``STATUS_COMPLETED``，
# 这样上游下游用任一别名都能在 Proto 上对齐。
# ---------------------------------------------------------------------------
_STATUS_STR_TO_PROTO: dict[str, int] = {
    "": data_pb2.Status.STATUS_UNSPECIFIED,
    "pending": data_pb2.Status.STATUS_PENDING,
    "running": data_pb2.Status.STATUS_RUNNING,
    "success": data_pb2.Status.STATUS_COMPLETED,
    "completed": data_pb2.Status.STATUS_COMPLETED,
    "done": data_pb2.Status.STATUS_COMPLETED,
    "failed": data_pb2.Status.STATUS_FAILED,
    "failure": data_pb2.Status.STATUS_FAILED,
    "error": data_pb2.Status.STATUS_FAILED,
    "cancelled": data_pb2.Status.STATUS_CANCELLED,
    "canceled": data_pb2.Status.STATUS_CANCELLED,
    "timeout": data_pb2.Status.STATUS_TIMEOUT,
    "timed_out": data_pb2.Status.STATUS_TIMEOUT,
}


_LOG_TYPE_STR_TO_PROTO: dict[str, int] = {
    "": data_pb2.LogType.LOG_TYPE_UNSPECIFIED,
    "stdout": data_pb2.LogType.LOG_TYPE_STDOUT,
    "stderr": data_pb2.LogType.LOG_TYPE_STDERR,
    "system": data_pb2.LogType.LOG_TYPE_SYSTEM,
}


def _status_str_to_proto(status: str) -> int:
    return _STATUS_STR_TO_PROTO.get((status or "").lower(), data_pb2.Status.STATUS_UNSPECIFIED)


def _log_type_str_to_proto(log_type: str) -> int:
    return _LOG_TYPE_STR_TO_PROTO.get((log_type or "").lower(), data_pb2.LogType.LOG_TYPE_UNSPECIFIED)


def _datetime_to_proto_timestamp(dt: datetime | None):
    """``datetime`` → ``common_pb2.Timestamp``。``None`` 时返回 ``None``。"""
    if dt is None:
        return None
    from antcode_contracts import common_pb2

    ts = common_pb2.Timestamp()
    epoch = dt.timestamp()
    ts.seconds = int(epoch)
    ts.nanos = int((epoch - int(epoch)) * 1e9)
    return ts


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
        redis_url: str = "redis://localhost:6379/0",
        worker_id: str | None = None,
        config: ServerConfig | None = None,
        namespace: str | None = None,
        consumer_group: str | None = None,
        control_group: str | None = None,
    ):
        super().__init__(config)
        self._redis_url = redis_url
        self._redis = None
        self._worker_id = worker_id
        resolved_namespace = namespace or getattr(self._config, "redis_namespace", None)
        self._keys = RedisKeys(namespace=resolved_namespace)
        self._consumer_group = consumer_group or self._keys.consumer_group_name()
        self._consumer_name = (
            self._keys.consumer_name(worker_id) if worker_id else "worker"
        )
        self._control_group = control_group or self._keys.consumer_group_name("control")
        self._reclaimer: PendingTaskReclaimer | None = None
        self._receipt_cache: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._poll_error_count = 0
        self._poll_backoff_until = 0.0
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

        import redis.asyncio as aioredis
        from redis.asyncio.retry import Retry
        from redis.backoff import ExponentialBackoff

        max_attempts = min(3, max(1, self._config.max_reconnect_attempts))
        delay = 0.3

        for attempt in range(1, max_attempts + 1):
            try:
                retry = Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=3)
                self._redis = aioredis.from_url(
                    self._redis_url,
                    retry_on_timeout=True,
                    retry=retry,
                    retry_on_error=[
                        ConnectionError,
                        TimeoutError,
                    ],
                    socket_timeout=10,
                    socket_connect_timeout=10,
                    socket_keepalive=True,
                    health_check_interval=30,
                    encoding="utf-8",
                    decode_responses=True,
                )

                # 测试连接
                await self._redis.ping()

                # 确保消费者组存在
                ready_stream = self._keys.task_ready_stream(self._worker_id)
                await ensure_consumer_group(
                    self._redis, ready_stream, self._consumer_group
                )

                # 控制通道消费者组
                await ensure_consumer_group(
                    self._redis, self._keys.control_stream(self._worker_id), self._control_group
                )
                await ensure_consumer_group(
                    self._redis, self._keys.control_global_stream(), self._control_group
                )

                # 启动 pending 回收器
                self._reclaimer = PendingTaskReclaimer(
                    redis_client=self._redis,
                    worker_id=self._worker_id,
                    keys=self._keys,
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

                logger.info(f"Redis 传输层已启动: {self._redis_url}")
                return True

            except Exception as e:
                if self._redis:
                    await self._redis.aclose()
                    self._redis = None
                if not self._is_connection_error(e) or attempt >= max_attempts:
                    logger.error(f"Redis 连接失败: {e}")
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

    async def poll_task(self, timeout: float = 5.0) -> TaskMessage | None:
        """
        从 Redis Streams 拉取任务

        使用 XREADGROUP 从 ready queue 读取任务。
        """
        if not self._redis or not self._running:
            return None

        try:
            now = time.monotonic()
            if self._poll_backoff_until > now:
                await asyncio.sleep(self._poll_backoff_until - now)

            stream_key = self._keys.task_ready_stream(self._worker_id)
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

            task_msg = TaskMessage(
                task_id=decoded.get("task_id", ""),
                project_id=decoded.get("project_id", ""),
                project_type=decoded.get("project_type", "code"),
                priority=int(decoded.get("priority", 0) or 0),
                params=decoded.get("params", {}) or {},
                environment=decoded.get("environment", {}) or {},
                timeout=int(decoded.get("timeout", 3600) or 3600),
                download_url=decoded.get("download_url", "") or "",
                file_hash=decoded.get("file_hash", "") or "",
                entry_point=decoded.get("entry_point", "") or "",
                is_compressed=decoded.get("is_compressed"),
                run_id=decoded.get("run_id", "") or "",
                receipt=receipt,
            )

            self._receipt_cache[receipt] = (stream_name, msg_id, decoded)
            return task_msg

        except Exception as e:
            self._poll_error_count += 1
            delay = min(30.0, 0.5 * (2 ** (self._poll_error_count - 1)))
            self._poll_backoff_until = time.monotonic() + delay
            logger.error(f"拉取任务失败: {e}")
            logger.warning(f"拉取任务退避 {delay:.1f}s (连续失败 {self._poll_error_count} 次)")
            if self._poll_error_count % 3 == 0:
                await self.reconnect()
            return None

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

            await self._run_with_reconnect(
                "确认任务",
                lambda: self._redis.xack(stream_key, self._consumer_group, msg_id),
            )
            self._receipt_cache.pop(task_id, None)
            return True

        except Exception as e:
            logger.error(f"确认任务失败: {e}")
            return False

    async def report_result(self, result: TaskResult) -> bool:
        """上报任务结果

        P1b：从 dict 切换到 Proto bytes（``data_pb2.TaskStatus``），写到
        ``task:result`` Stream 的单字段 ``PROTO_FIELD``。Master ``result_loop``
        通过 ``ProtoCodec(TaskStatus)`` 解码。
        """
        if not self._redis or not self._running:
            return False

        try:
            result_key = self._keys.task_result_stream()
            ts_msg = data_pb2.TaskStatus(
                run_id=result.run_id or "",
                task_id=result.task_id or "",
                worker_id=self._worker_id or "",
                status=_status_str_to_proto(result.status),
                exit_code=int(result.exit_code or 0),
                error_message=result.error_message or "",
                duration_ms=int(result.duration_ms or 0),
            )
            started_ts = _datetime_to_proto_timestamp(result.started_at)
            if started_ts is not None:
                ts_msg.started_at.CopyFrom(started_ts)
            finished_ts = _datetime_to_proto_timestamp(result.finished_at)
            if finished_ts is not None:
                ts_msg.finished_at.CopyFrom(finished_ts)
            # data: dict → map<string,string>。强制 str 化（Proto map<string,string>
            # 要求 str；非 str 用 to_json/repr fallback）。
            if result.data:
                for k, v in result.data.items():
                    key = str(k)
                    if isinstance(v, (str, int, float, bool)):
                        ts_msg.data[key] = str(v)
                    else:
                        try:
                            ts_msg.data[key] = json.dumps(v, ensure_ascii=False)
                        except Exception:
                            ts_msg.data[key] = repr(v)

            payload_bytes = ts_msg.SerializeToString()

            await self._run_with_reconnect(
                "上报结果",
                lambda: self._redis.xadd(result_key, {PROTO_FIELD: payload_bytes}),
            )
            return True

        except Exception as e:
            logger.error(f"上报结果失败: {e}")
            return False

    async def requeue_task(self, receipt: str, reason: str = "") -> bool:
        """重新入队任务"""
        if not self._redis or not self._running:
            return False

        try:
            cached = self._receipt_cache.get(receipt)
            if not cached:
                return False

            stream_key, msg_id, data = cached
            data = dict(data)
            data["requeue_reason"] = reason
            data["requeue_at"] = datetime.now().isoformat()

            await self._run_with_reconnect(
                "任务重新入队",
                lambda: self._redis.xadd(stream_key, data),
            )
            await self._run_with_reconnect(
                "重新入队确认",
                lambda: self._redis.xack(stream_key, self._consumer_group, msg_id),
            )
            self._receipt_cache.pop(receipt, None)
            return True
        except Exception as e:
            logger.error(f"重新入队失败: {e}")
            return False

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
            log_type=_log_type_str_to_proto(log.log_type),
            content=log.content or "",
            sequence=int(log.sequence or 0),
        )
        ts = _datetime_to_proto_timestamp(log.timestamp or datetime.now())
        if ts is not None:
            entry.timestamp.CopyFrom(ts)
        return entry

    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        """发送批量日志

        P1b：每个 ``run_id`` 对应一个 ``LogBatch`` Proto 消息，写到该 run_id 的
        log Stream 的单字段 ``PROTO_FIELD``。Master ``log_ingest_loop`` 通过
        ``ProtoCodec(LogBatch)`` 解码。

        注：原实现按 ``ms-sequence`` 构造 Stream entry id 做幂等去重；切换到
        Proto bytes 单字段后 entry id 改回 ``*``（Master 端通过 ``run_id`` /
        ``sequence`` 字段判重）。这是 P1b 已知的取舍，详见 task 描述。
        """
        if not self._redis or not self._running:
            return False

        if not logs:
            return True

        try:
            maxlen = self._keys.config.stream_max_len
            ttl_seconds = self._keys.config.log_ttl

            # 按 run_id 分组打包，每个 run_id 一条 Proto LogBatch
            batches: dict[str, list[LogMessage]] = {}
            for log in logs:
                batches.setdefault(log.run_id or "", []).append(log)

            async def _write_batches():
                pipe = self._redis.pipeline(transaction=False)
                seen = set()
                for run_id, run_logs in batches.items():
                    log_key = self._keys.log_stream(run_id)
                    batch_msg = data_pb2.LogBatch(
                        worker_id=self._worker_id or "",
                    )
                    for log in run_logs:
                        batch_msg.entries.append(self._build_log_entry_proto(log))
                    payload_bytes = batch_msg.SerializeToString()
                    if maxlen > 0:
                        pipe.xadd(
                            log_key,
                            {PROTO_FIELD: payload_bytes},
                            maxlen=maxlen,
                            approximate=self._keys.config.stream_approx_max_len,
                        )
                    else:
                        pipe.xadd(log_key, {PROTO_FIELD: payload_bytes})
                    if ttl_seconds > 0 and log_key not in seen:
                        pipe.expire(log_key, ttl_seconds)
                        seen.add(log_key)

                return await pipe.execute(raise_on_error=False)

            results = await self._run_with_reconnect("发送批量日志", _write_batches)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"发送批量日志失败: {result}")
                    return False
            return True
        except Exception as e:
            logger.error(f"发送批量日志失败: {e}")
            return False

    async def send_log_chunk(
        self,
        run_id: str,
        log_type: str,
        data: bytes,
        offset: int,
        is_final: bool = False,
    ) -> bool:
        """发送日志分片

        TODO(P3): chunk Stream 暂时保留 dict + base64 wire format（契约测试
        ``test_send_log_chunk_concat_round_trips`` 按字段读 ``data`` /
        ``offset`` / ``is_final``）。P3 会把 chunk 归并入 ``LogBatch``
        并由 source_bundle/pgartifact 承载大块。
        """
        if not self._redis or not self._running:
            return False

        try:
            # 写入 log chunk stream
            chunk_key = self._keys.log_chunk_stream(run_id)
            fields = {
                "log_type": log_type,
                "data": base64.b64encode(data).decode("utf-8"),
                "offset": str(offset),
                "is_final": str(is_final).lower(),
                "timestamp": datetime.now().isoformat(),
            }
            maxlen = self._keys.config.stream_max_len
            async def _write_chunk():
                pipe = self._redis.pipeline(transaction=False)
                if maxlen > 0:
                    pipe.xadd(
                        chunk_key,
                        fields,
                        maxlen=maxlen,
                        approximate=self._keys.config.stream_approx_max_len,
                    )
                else:
                    pipe.xadd(chunk_key, fields)
                if self._keys.config.log_ttl > 0:
                    pipe.expire(chunk_key, self._keys.config.log_ttl)
                await pipe.execute()

            await self._run_with_reconnect("发送日志分片", _write_chunk)
            return True

        except Exception as e:
            logger.error(f"发送日志分片失败: {e}")
            return False

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
            logger.error(f"发送心跳失败: {exc}")
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

            pipe = self._redis.pipeline(transaction=False)
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
            streams = {
                self._keys.control_stream(self._worker_id): ">",
                self._keys.control_global_stream(): ">",
            }
            results = await self._redis.xreadgroup(
                groupname=self._control_group,
                consumername=self._consumer_name,
                streams=streams,
                count=1,
                block=int(timeout * 1000),
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
        except Exception as e:
            logger.error(f"拉取控制消息失败: {e}")
            return None

    async def ack_control(self, receipt: str) -> bool:
        """确认控制消息"""
        if not self._redis or not self._running:
            return False

        try:
            stream_key, msg_id = self._decode_receipt(receipt)
            if not stream_key:
                return False
            await self._redis.xack(stream_key, self._control_group, msg_id)
            return True
        except Exception as e:
            logger.error(f"确认控制消息失败: {e}")
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
            await self._redis.xadd(reply_stream, payload, maxlen=1, approximate=True)
            await self._redis.expire(reply_stream, 120)
            return True
        except Exception as e:
            logger.error(f"回传控制结果失败: {e}")
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
        except Exception as e:
            logger.error(f"上报爬虫数据失败: {e}")
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

            await self._redis.hset(meta_key, mapping=data)

            if ttl_seconds > 0:
                await self._redis.expire(meta_key, ttl_seconds)

            return True
        except Exception as e:
            logger.error(f"更新爬虫元数据失败: {e}")
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
                key = key.decode("utf-8", errors="ignore")
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            decoded[key] = value

        for field in ("params", "environment", "data", "payload"):
            if field in decoded and isinstance(decoded[field], str):
                with contextlib.suppress(Exception):
                    decoded[field] = json.loads(decoded[field])

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
