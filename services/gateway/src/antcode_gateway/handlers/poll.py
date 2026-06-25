"""
任务轮询处理器

从 Redis Streams ready queues 读取任务，转码为 ``data_pb2.TaskDispatch`` 供
``DataService.StreamTasks`` 推送给 Worker。

P1c 改造：保留 ready stream 的 JSON 帧（写入端 Master 当前仍是 JSON 派发），
gateway 在 yield 给 Worker 前转码为 Proto，避免端到端的 JSON 暴露。

待 Master 切换为 Proto bytes 派发后，``_parse_task_data`` 可以替换为
``StreamClient(codec=ProtoCodec(data_pb2.TaskDispatch))`` 路径。

**Validates: Requirements 6.5**
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis import (
    decode_stream_payload,
    redis_namespace,
    task_ready_stream,
    worker_group,
)
from loguru import logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


@dataclass
class TaskInfo:
    """任务信息（保持向后兼容的 dataclass，方便单元测试）"""

    task_id: str
    project_id: str
    run_id: str = ""
    project_type: str = "spider"
    priority: int = 0
    timeout: int = 3600
    download_url: str = ""
    file_hash: str = ""
    entry_point: str = ""
    params: dict[str, object] = field(default_factory=dict)
    environment: dict[str, object] = field(default_factory=dict)
    receipt_id: str = ""


def task_info_to_dispatch(task: TaskInfo) -> data_pb2.TaskDispatch:
    """把内部 ``TaskInfo`` 转码为 Proto ``TaskDispatch`` 供 StreamTasks 推送。

    ready stream 当前的 ``download_url`` / ``file_hash`` 暂时映射到
    ``source_bundle_uri`` / ``source_bundle_sha256`` 字段（Master 切换 source bundle
    派发后会自然对齐）。
    """
    dispatch = data_pb2.TaskDispatch(
        task_id=task.task_id,
        project_id=task.project_id,
        project_type=task.project_type,
        priority=int(task.priority),
        timeout_seconds=int(task.timeout),
        source_bundle_uri=task.download_url,
        source_bundle_sha256=task.file_hash,
        transfer_method="source_bundle" if task.download_url else "",
        entry_point=task.entry_point,
        run_id=task.run_id,
        receipt_id=task.receipt_id,
    )
    for key, value in (task.params or {}).items():
        dispatch.params[str(key)] = str(value) if value is not None else ""
    for key, value in (task.environment or {}).items():
        dispatch.environment[str(key)] = str(value) if value is not None else ""
    return dispatch


class TaskPollHandler:
    """任务轮询处理器

    从 Redis Streams ready queues 读取任务。
    Gateway 不实现调度策略，只负责代理队列读取。
    """

    READY_QUEUE_PREFIX = f"{redis_namespace()}:task:ready:"
    WORKER_GROUP = worker_group()

    def __init__(self, redis_client=None):
        self._redis_client = redis_client
        self._group_lock = asyncio.Lock()
        self._initialized_groups: set[tuple[str, str]] = set()

    async def _ensure_consumer_group(self, redis, stream_key: str, group: str) -> None:
        key = (stream_key, group)
        if key in self._initialized_groups:
            return

        async with self._group_lock:
            if key in self._initialized_groups:
                return

            try:
                await redis.xgroup_create(stream_key, group, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

            self._initialized_groups.add(key)

    async def _get_redis_client(self):
        if self._redis_client is None:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await get_redis_client()
            except ImportError:
                logger.warning("antcode_core.infrastructure.redis 不可用")
                return None
        return self._redis_client

    async def handle(
        self,
        worker_id: str,
        max_tasks: int = 1,
        block_ms: int = 5000,
        queues: list[str] | None = None,
    ) -> list[TaskInfo]:
        """处理任务轮询请求（同步式拉取，用于 StreamTasks 内部循环）。"""
        logger.debug(
            f"Worker {worker_id} 轮询任务，最多 {max_tasks} 个，阻塞 {block_ms}ms"
        )

        redis = await self._get_redis_client()
        if redis is None:
            logger.warning("Redis 不可用，返回空任务列表")
            return []

        try:
            if queues is None:
                queues = [task_ready_stream(worker_id)]

            for queue in queues:
                await self._ensure_consumer_group(redis, queue, self.WORKER_GROUP)

            streams = dict.fromkeys(queues, ">")

            results = await redis.xreadgroup(
                groupname=self.WORKER_GROUP,
                consumername=worker_id,
                streams=streams,
                count=max_tasks,
                block=block_ms,
            )

            if not results:
                return []

            tasks = []
            for stream_name, messages in results:
                for message_id, data in messages:
                    task = self._parse_task_data(data, message_id)
                    if task:
                        sname = (
                            stream_name.decode()
                            if isinstance(stream_name, bytes)
                            else str(stream_name)
                        )
                        mid = (
                            message_id.decode()
                            if isinstance(message_id, bytes)
                            else str(message_id)
                        )
                        task.receipt_id = f"{sname}|{mid}"
                        tasks.append(task)
                        logger.debug(
                            f"读取任务: task_id={task.task_id}, stream={sname}, message_id={mid}"
                        )

            logger.info(f"Worker {worker_id} 获取了 {len(tasks)} 个任务")
            return tasks

        except Exception as exc:
            logger.error(f"读取任务失败: {exc}")
            return []

    def _parse_task_data(self, data: dict, message_id: object) -> TaskInfo | None:
        try:
            decoded = decode_stream_payload(data)

            task_id = decoded.get("task_id")
            if not task_id:
                logger.warning(f"任务数据缺少 task_id: {message_id}")
                return None

            return TaskInfo(
                task_id=task_id,
                project_id=decoded.get("project_id", ""),
                run_id=decoded.get("run_id", ""),
                project_type=decoded.get("project_type", "spider"),
                priority=int(decoded.get("priority", 0)),
                timeout=int(decoded.get("timeout", 3600)),
                download_url=decoded.get("download_url", ""),
                file_hash=decoded.get("file_hash", ""),
                entry_point=decoded.get("entry_point", ""),
                params=self._parse_json(decoded.get("params", "{}")),
                environment=self._parse_json(decoded.get("environment", "{}")),
            )

        except Exception as exc:
            logger.error(f"解析任务数据失败: {exc}, message_id={message_id}")
            return None

    def _parse_json(self, value: object) -> dict[str, object]:
        if not value:
            return {}

        if isinstance(value, dict):
            return value

        raw = (
            value.decode("utf-8", errors="ignore")
            if isinstance(value, (bytes, bytearray))
            else str(value)
        )
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def ack_task(self, worker_id: str, queue: str, message_id: str) -> bool:
        redis = await self._get_redis_client()
        if redis is None:
            return False

        try:
            await redis.xack(queue, self.WORKER_GROUP, message_id)
            logger.debug(
                f"任务已确认: worker_id={worker_id}, queue={queue}, message_id={message_id}"
            )
            return True
        except Exception as exc:
            logger.error(f"确认任务失败: {exc}")
            return False

    async def ack_receipt(
        self,
        receipt_id: str,
        accepted: bool = True,
        reason: str = "",
    ) -> bool:
        """确认任务（receipt 形式）"""
        if "|" not in receipt_id:
            return False
        queue, message_id = receipt_id.split("|", 1)
        if accepted:
            return await self.ack_task("", queue, message_id)
        return await self._requeue_task(queue, message_id, reason)

    async def _requeue_task(self, queue: str, message_id: str, reason: str) -> bool:
        """拒绝任务并回写 ready stream。

        注：保留 JSON 帧以兼容 Master 当前的 ``_send_batch_to_queue`` 写入端。
        待 Master 切换为 ``ProtoCodec(TaskDispatch)`` 派发后，这里需要改为
        ``xadd {PROTO_FIELD: TaskDispatch.SerializeToString()}``。
        """
        redis = await self._get_redis_client()
        if redis is None:
            return False

        try:
            messages = await redis.xrange(queue, min=message_id, max=message_id, count=1)
            if not messages:
                return False

            _, data = messages[0]
            decoded = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in data.items()
            }
            decoded["requeue_reason"] = reason
            decoded["requeue_at"] = datetime.now().isoformat()

            await redis.xadd(queue, decoded)
            await redis.xack(queue, self.WORKER_GROUP, message_id)
            return True
        except Exception as exc:
            logger.error(f"重新入队失败: {exc}")
            return False
