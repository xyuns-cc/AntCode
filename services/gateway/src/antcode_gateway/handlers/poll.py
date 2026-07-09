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
from antcode_core.observability.tracing import inject_trace
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

    P1-#6: 把 TaskInfo 上携带的 ``trace_parent`` 写到 dispatch.trace,
    让 worker 端拿到 W3C traceparent 后能续接。``TaskInfo`` 当前没正式
    声明 ``trace_parent`` 字段(归 Agent P 改 contracts/core), 这里用
    getattr 做 fallback 拿空字符串。
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
    inject_trace(dispatch, traceparent=getattr(task, "trace_parent", "") or "")
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

            # B3: 先排空 PEL —— worker 崩溃或断线时，之前 XREADGROUP 到但未
            # 发出 AckTask 的消息挂在该 consumer 的 PEL 下。旧实现直接读 ">"
            # 只会拉新消息，PEL 里的孤儿永远无人回收（gateway 侧无 XAUTOCLAIM
            # loop）。重连时先用 "0" 读一遍 pending，把它们交回给 worker
            # 处理（幂等由 worker B2 SET NX 保护），然后再读 ">"。
            pel_results = await self._drain_pending_streams(
                redis, queues, worker_id, max_tasks
            )

            streams = dict.fromkeys(queues, ">")

            live_results = await redis.xreadgroup(
                groupname=self.WORKER_GROUP,
                consumername=worker_id,
                streams=streams,
                count=max_tasks,
                block=block_ms,
            )

            results = list(pel_results) + list(live_results or [])
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
            logger.exception(f"读取任务失败: {exc}")
            return []

    async def _drain_pending_streams(
        self,
        redis,
        queues: list[str],
        worker_id: str,
        max_tasks: int,
    ) -> list:
        """B3: 排空 (worker) 每个 stream 上的 PEL 到本次响应，让重连的 worker
        真正拿到自己之前未 ack 的消息。仅返回不为空的 stream。
        """
        pel_streams = dict.fromkeys(queues, "0")
        try:
            pel = await redis.xreadgroup(
                groupname=self.WORKER_GROUP,
                consumername=worker_id,
                streams=pel_streams,
                count=max_tasks,
                block=0,
            )
        except Exception as exc:
            logger.warning(f"排空 PEL 失败(继续读新消息): worker={worker_id} err={exc}")
            return []
        if not pel:
            return []
        # 过滤空 messages 的 stream；有则 log 一下方便运维排查
        result = [(name, msgs) for name, msgs in pel if msgs]
        if result:
            total = sum(len(msgs) for _, msgs in result)
            logger.info(f"B3 PEL 排空: worker={worker_id} 找到 {total} 条孤儿消息")
        return result

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
            logger.exception(f"解析任务数据失败: {exc}, message_id={message_id}")
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
            logger.exception(f"确认任务失败: {exc}")
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

    # B7: 与 Direct 模式（redis/transport.py:MAX_REQUEUE_COUNT）对齐；
    # 超阈值的消息进入死信 stream 而非无限重投。
    MAX_REQUEUE_COUNT = 5
    DEAD_LETTER_STREAM_SUFFIX = "task:dead_letter"
    DEAD_LETTER_MAXLEN = 10000

    async def _requeue_task(self, queue: str, message_id: str, reason: str) -> bool:
        """拒绝任务并回写 ready stream；超过 ``MAX_REQUEUE_COUNT`` 次进死信。

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

            # B7: 计数 + 死信路径
            try:
                requeue_count = int(decoded.get("requeue_count", "0") or "0")
            except (TypeError, ValueError):
                requeue_count = 0
            requeue_count += 1

            now_iso = datetime.now().isoformat()
            if requeue_count > self.MAX_REQUEUE_COUNT:
                dead_payload = dict(decoded)
                dead_payload["requeue_count"] = str(requeue_count)
                dead_payload["last_requeue_reason"] = reason
                dead_payload["dead_letter_at"] = now_iso
                dead_payload["origin_queue"] = queue
                dead_letter_key = f"antcode:{self.DEAD_LETTER_STREAM_SUFFIX}"
                try:
                    await redis.xadd(
                        dead_letter_key,
                        dead_payload,
                        maxlen=self.DEAD_LETTER_MAXLEN,
                        approximate=True,
                    )
                    await redis.xack(queue, self.WORKER_GROUP, message_id)
                    logger.warning(
                        f"消息进入死信(超过 {self.MAX_REQUEUE_COUNT} 次 requeue): "
                        f"queue={queue} msg={message_id} reason={reason}"
                    )
                    return True
                except Exception as dl_exc:
                    logger.exception(f"死信写入失败: {dl_exc}")
                    # 死信失败时不 ack，让 PEL/reclaim 兜底
                    return False

            decoded["requeue_count"] = str(requeue_count)
            decoded["requeue_reason"] = reason
            decoded["requeue_at"] = now_iso

            await redis.xadd(queue, decoded)
            await redis.xack(queue, self.WORKER_GROUP, message_id)
            return True
        except Exception as exc:
            logger.exception(f"重新入队失败: {exc}")
            return False
