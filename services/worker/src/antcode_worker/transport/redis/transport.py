"""
Redis 传输层实现（Direct 模式）

内网 Worker 直连 Redis Streams，低延迟。

Requirements: 5.3, 7.2, 11.3
"""

import asyncio
import contextlib
import json
import time
from datetime import datetime
from typing import Any

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
    ):
        super().__init__(config)
        self._redis_url = redis_url
        self._redis = None
        self._worker_id = worker_id
        self._keys = RedisKeys()
        self._consumer_group = self._keys.consumer_group_name()
        self._consumer_name = (
            self._keys.consumer_name(worker_id) if worker_id else "worker"
        )
        self._control_group = self._keys.consumer_group_name("control")
        self._reclaimer: PendingTaskReclaimer | None = None
        self._receipt_cache: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._poll_error_count = 0
        self._poll_backoff_until = 0.0

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
                run_id=decoded.get("run_id", "") or decoded.get("execution_id", "") or "",
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
        """上报任务结果"""
        if not self._redis or not self._running:
            return False

        try:
            result_key = self._keys.task_result_stream()
            payload = {
                "run_id": result.run_id,
                "task_id": result.task_id,
                "status": result.status,
                "exit_code": str(result.exit_code),
                "error_message": result.error_message,
                "started_at": result.started_at.isoformat() if result.started_at else "",
                "finished_at": result.finished_at.isoformat() if result.finished_at else "",
                "duration_ms": str(result.duration_ms),
            }
            if result.data:
                payload["data"] = json.dumps(result.data, ensure_ascii=False)
            await self._run_with_reconnect(
                "上报结果",
                lambda: self._redis.xadd(result_key, payload),
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
        """发送实时日志"""
        if not self._redis or not self._running:
            return False

        try:
            log_key = self._keys.log_stream(log.execution_id)
            timestamp = log.timestamp or datetime.now()
            fields = {
                "log_type": log.log_type,
                "content": log.content,
                "timestamp": timestamp.isoformat(),
                "sequence": str(log.sequence),
            }
            entry_id = self._build_log_entry_id(log, timestamp) or "*"
            maxlen = self._keys.config.stream_max_len

            async def _write_log():
                if maxlen > 0:
                    await self._redis.xadd(
                        log_key,
                        fields,
                        id=entry_id,
                        maxlen=maxlen,
                        approximate=self._keys.config.stream_approx_max_len,
                    )
                else:
                    await self._redis.xadd(log_key, fields, id=entry_id)
                if self._keys.config.log_ttl > 0:
                    await self._redis.expire(log_key, self._keys.config.log_ttl)

            await self._run_with_reconnect("发送日志", _write_log)
            return True

        except Exception as e:
            if self._is_duplicate_log_error(e):
                logger.debug(f"日志已存在，忽略重复写入: {log.execution_id} seq={log.sequence}")
                return True
            logger.error(f"发送日志失败: {e}")
            return False

    async def send_log_batch(self, logs: list[LogMessage]) -> bool:
        """发送批量日志"""
        if not self._redis or not self._running:
            return False

        if not logs:
            return True

        try:
            pipe = self._redis.pipeline()
            maxlen = self._keys.config.stream_max_len
            ttl_seconds = self._keys.config.log_ttl
            seen = set()
            for log in logs:
                log_key = self._keys.log_stream(log.execution_id)
                timestamp = log.timestamp or datetime.now()
                fields = {
                    "log_type": log.log_type,
                    "content": log.content,
                    "timestamp": timestamp.isoformat(),
                    "sequence": str(log.sequence),
                }
                entry_id = self._build_log_entry_id(log, timestamp) or "*"
                if maxlen > 0:
                    pipe.xadd(
                        log_key,
                        fields,
                        id=entry_id,
                        maxlen=maxlen,
                        approximate=self._keys.config.stream_approx_max_len,
                    )
                else:
                    pipe.xadd(log_key, fields, id=entry_id)
                if ttl_seconds > 0 and log_key not in seen:
                    pipe.expire(log_key, ttl_seconds)
                    seen.add(log_key)
            results = await pipe.execute(raise_on_error=False)
            for result in results:
                if isinstance(result, Exception):
                    if self._is_duplicate_log_error(result):
                        continue
                    logger.error(f"发送批量日志失败: {result}")
                    return False
            return True
        except Exception as e:
            logger.error(f"发送批量日志失败: {e}")
            return False

    @staticmethod
    def _build_log_entry_id(log: LogMessage, timestamp: datetime) -> str | None:
        if log.sequence is None:
            return None
        try:
            seq = int(log.sequence)
        except (TypeError, ValueError):
            return None
        ts_ms = int(timestamp.timestamp() * 1000)
        return f"{ts_ms}-{seq}"

    @staticmethod
    def _is_duplicate_log_error(error: Exception) -> bool:
        message = str(error)
        return "ID specified in XADD" in message or "equal or smaller" in message

    async def send_log_chunk(
        self,
        execution_id: str,
        log_type: str,
        data: bytes,
        offset: int,
        is_final: bool = False,
    ) -> bool:
        """发送日志分片"""
        if not self._redis or not self._running:
            return False

        try:
            import base64

            # 写入 log chunk stream
            chunk_key = self._keys.log_chunk_stream(execution_id)
            fields = {
                "log_type": log_type,
                "data": base64.b64encode(data).decode("utf-8"),
                "offset": str(offset),
                "is_final": str(is_final).lower(),
                "timestamp": datetime.now().isoformat(),
            }
            maxlen = self._keys.config.stream_max_len
            if maxlen > 0:
                await self._redis.xadd(
                    chunk_key,
                    fields,
                    maxlen=maxlen,
                    approximate=self._keys.config.stream_approx_max_len,
                )
            else:
                await self._redis.xadd(chunk_key, fields)
            if self._keys.config.log_ttl > 0:
                await self._redis.expire(chunk_key, self._keys.config.log_ttl)
            return True

        except Exception as e:
            logger.error(f"发送日志分片失败: {e}")
            return False

    async def send_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """发送心跳"""
        if not self._redis or not self._running:
            return False

        try:
            import json

            # 支持两种心跳格式：HeartbeatMessage 和 HeartbeatReporter 的 Heartbeat
            # 提取字段值，兼容不同的心跳对象结构
            worker_id = getattr(heartbeat, "worker_id", None)
            status = getattr(heartbeat, "status", "online")
            timestamp = getattr(heartbeat, "timestamp", None) or datetime.now()

            # 尝试从 metrics 属性获取指标（HeartbeatReporter 的 Heartbeat 格式）
            metrics = getattr(heartbeat, "metrics", None)
            if metrics is not None:
                cpu_percent = getattr(metrics, "cpu", 0.0)
                memory_percent = getattr(metrics, "memory", 0.0)
                disk_percent = getattr(metrics, "disk", 0.0)
                running_tasks = getattr(metrics, "running_tasks", 0)
                max_concurrent_tasks = getattr(metrics, "max_concurrent_tasks", 5)
            else:
                # 直接从心跳对象获取（HeartbeatMessage 格式）
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

            # 写入 heartbeat hash
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

                await self._redis.hset(
                    hb_key,
                    mapping=mapping,
                )
                await self._redis.expire(hb_key, self._config.heartbeat_interval * 3)

            await self._run_with_reconnect("发送心跳", _write_heartbeat)
            return True

        except Exception as e:
            logger.error(f"发送心跳失败: {e}")
            return False

    async def poll_control(self, timeout: float = 5.0) -> ControlMessage | None:
        """拉取控制消息"""
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
                run_id=decoded.get("run_id", decoded.get("execution_id", "")),
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
        """回传控制结果"""
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
