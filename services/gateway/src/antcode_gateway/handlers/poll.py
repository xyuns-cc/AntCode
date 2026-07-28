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

from antcode_gateway.handlers.task_settle import TaskSettlementMixin

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


@dataclass
class TaskInfo:
    """与当前 ready stream 契约一致的任务信息。"""

    task_id: str
    project_id: str
    run_id: str = ""
    project_type: str = "spider"
    priority: int = 0
    timeout: int = 3600
    source_bundle_uri: str = ""
    source_bundle_sha256: str = ""
    source_bundle_size: int = 0
    transfer_method: str = ""
    resolved_revision: str = ""
    source_subdir: str = ""
    entry_point: str = ""
    runtime_env_name: str = ""
    params: dict[str, object] = field(default_factory=dict)
    environment: dict[str, object] = field(default_factory=dict)
    receipt_id: str = ""
    trace_parent: str = ""


def _pb_scalar_for(value: object) -> str:
    """P1-24: 把嵌套 params/environment 值序列化成字符串。

    ``TaskDispatch.params`` / ``.environment`` proto 是 ``map<string,string>``,
    非字符串值必须先编码。旧实现 ``str(value)`` 对 dict/list 会得到 Python
    ``repr``（``"{'a': 1}"``），worker 侧 ``json.loads`` 会抛。改用 ``json.dumps``
    保证 round-trip 安全。
    """
    if value is None:
        scalar = ""
    elif isinstance(value, str):
        scalar = value
    elif isinstance(value, (bytes, bytearray)):
        scalar = value.decode("utf-8", errors="replace")
    elif isinstance(value, bool):
        scalar = "true" if value else "false"
    elif isinstance(value, (int, float)):
        scalar = str(value)
    else:
        try:
            scalar = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            scalar = str(value)
    return scalar


def task_info_to_dispatch(task: TaskInfo) -> data_pb2.TaskDispatch:
    """把内部 ``TaskInfo`` 转码为 Proto ``TaskDispatch`` 供 Worker 消费。"""
    src_uri = task.source_bundle_uri or ""
    src_sha = task.source_bundle_sha256 or ""
    src_size = int(task.source_bundle_size or 0)
    transfer_method = task.transfer_method or ("source_bundle" if src_uri else "")

    dispatch = data_pb2.TaskDispatch(
        task_id=task.task_id,
        project_id=task.project_id,
        project_type=task.project_type,
        priority=int(task.priority),
        timeout_seconds=int(task.timeout),
        source_bundle_uri=src_uri,
        source_bundle_sha256=src_sha,
        source_bundle_size=src_size,
        transfer_method=transfer_method,
        resolved_revision=task.resolved_revision or "",
        source_subdir=task.source_subdir or "",
        entry_point=task.entry_point,
        run_id=task.run_id,
        receipt_id=task.receipt_id,
        runtime_env_name=task.runtime_env_name,
    )
    # P1-24: 用 json.dumps 而不是 str(value)，避免嵌套 dict 变 Python repr。
    for key, value in (task.params or {}).items():
        dispatch.params[str(key)] = _pb_scalar_for(value)
    for key, value in (task.environment or {}).items():
        dispatch.environment[str(key)] = _pb_scalar_for(value)
    inject_trace(dispatch, traceparent=getattr(task, "trace_parent", "") or "")
    return dispatch


class TaskPollHandler(TaskSettlementMixin):
    """任务轮询处理器

    从 Redis Streams ready queues 读取任务。
    Gateway 不实现调度策略，只负责代理队列读取；结算面（ACK/requeue/DLQ
    的代际 fence）在 ``TaskSettlementMixin``。
    """

    READY_QUEUE_PREFIX = f"{redis_namespace()}:task:ready:"
    WORKER_GROUP = worker_group()
    PENDING_VISIBILITY_TIMEOUT_MS = 30_000

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
        *,
        lease_id: str,
        max_tasks: int = 1,
        block_ms: int = 5000,
        queues: list[str] | None = None,
    ) -> list[TaskInfo]:
        """处理任务轮询请求（同步式拉取，用于 StreamTasks 内部循环）。"""
        logger.debug(f"Worker {worker_id} 轮询任务，最多 {max_tasks} 个，阻塞 {block_ms}ms")

        redis = await self._get_redis_client()
        if redis is None:
            raise RuntimeError("Redis 不可用，无法轮询任务")

        consumer = self.generation_consumer(worker_id, lease_id)
        queue_names = queues or [task_ready_stream(worker_id)]
        results = await self._read_streams(
            redis,
            queue_names,
            consumer,
            max_tasks=max_tasks,
            block_ms=block_ms,
        )
        tasks = await self._tasks_from_results(redis, results, worker_id)
        logger.info(f"Worker {worker_id} 获取了 {len(tasks)} 个任务")
        return tasks

    async def _read_streams(
        self,
        redis,
        queues: list[str],
        consumer: str,
        *,
        max_tasks: int,
        block_ms: int,
    ) -> list:
        for queue in queues:
            await self._ensure_consumer_group(redis, queue, self.WORKER_GROUP)
        pending = await self._drain_pending_streams(
            redis,
            queues,
            consumer,
            max_tasks=max_tasks,
        )
        live = await redis.xreadgroup(
            groupname=self.WORKER_GROUP,
            consumername=consumer,
            streams=dict.fromkeys(queues, ">"),
            count=max_tasks,
            block=block_ms,
        )
        return [*pending, *(live or [])]

    async def _tasks_from_results(self, redis, results: list, worker_id: str) -> list[TaskInfo]:
        tasks = []
        for stream_name, messages in results:
            stream = self._decode_identifier(stream_name)
            for message_id, data in messages:
                message = self._decode_identifier(message_id)
                try:
                    task = self._parse_task_data(data, message_id)
                except Exception as exc:
                    # P2-08：毒消息除了 XACK 之外**必须**先写 DLQ，保留原始帧
                    # 供诊断和后续重放。之前直接 XACK 丢弃 → 损坏帧/协议不兼容/
                    # schema 变化会永久删除任务，只能靠 master 180s reconcile
                    # 标 FAILED，运维再也看不到原文。
                    # 沿用同文件已有 DLQ 常量：antcode:task:dead_letter，
                    # maxlen=DEAD_LETTER_MAXLEN 保护 stream。
                    logger.warning(
                        f"毒任务消息进 DLQ 并 ACK: worker={worker_id} stream={stream} message_id={message} err={exc}"
                    )
                    dead_lettered = False
                    try:
                        dead_payload = {
                            self._decode_identifier(k): self._decode_identifier(v) for k, v in (data or {}).items()
                        }
                        dead_payload["dead_letter_at"] = datetime.now().isoformat()
                        dead_payload["dead_letter_reason"] = f"parse_error:{type(exc).__name__}"
                        dead_payload["origin_stream"] = stream
                        dead_payload["origin_msg_id"] = message
                        await redis.xadd(
                            self._dead_letter_key(stream),
                            dead_payload,
                            maxlen=self.DEAD_LETTER_MAXLEN,
                            approximate=True,
                        )
                        dead_lettered = True
                    except Exception:
                        logger.exception(f"毒消息写 DLQ 失败: stream={stream} message_id={message}")
                    if dead_lettered:
                        try:
                            await redis.xack(stream, self.WORKER_GROUP, message)
                        except Exception:
                            logger.exception(f"毒消息 ACK 失败: stream={stream} message_id={message}")
                    continue
                task.receipt_id = f"{stream}|{message}"
                tasks.append(task)
                logger.debug(f"读取任务: task_id={task.task_id}, stream={stream}, message_id={message}")
        return tasks

    @staticmethod
    def _decode_identifier(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    async def _drain_pending_streams(
        self,
        redis,
        queues: list[str],
        consumer: str,
        *,
        max_tasks: int,
    ) -> list:
        """认领超过 visibility timeout 的 PEL，避免活跃任务被紧循环重投。

        P1-GW-01: 以代际 consumer 认领。旧代际（或旧布局裸 worker_id
        consumer）滞留的 entry 会在 visibility timeout 后转移到当前代际
        consumer 名下，旧代际随后的结算被 Lua 的 consumer 校验拒绝。
        """
        result: list[tuple[str, list]] = []
        remaining = max_tasks
        for queue in queues:
            if remaining <= 0:
                break
            # Redis < 7.0 只返回 (next_id, messages) 两元组，>= 7.0 追加
            # deleted_ids 第三项；宽容解包，避免旧版本每次 poll 都抛 ValueError。
            claimed = await redis.xautoclaim(
                queue,
                self.WORKER_GROUP,
                consumer,
                self.PENDING_VISIBILITY_TIMEOUT_MS,
                start_id="0-0",
                count=remaining,
            )
            messages = claimed[1] if len(claimed) > 1 else []
            if not messages:
                continue
            result.append((queue, messages))
            remaining -= len(messages)
        if result:
            total = sum(len(msgs) for _, msgs in result)
            logger.info(f"B3 PEL visibility reclaim: consumer={consumer} 找到 {total} 条消息")
        return result

    def _parse_task_data(self, data: dict, message_id: object) -> TaskInfo:
        decoded = decode_stream_payload(data)
        task_id = decoded.get("task_id")
        if not task_id:
            raise ValueError(f"任务数据缺少 task_id: {message_id}")
        return TaskInfo(
            task_id=str(task_id),
            project_id=str(decoded.get("project_id", "")),
            run_id=str(decoded.get("run_id", "")),
            project_type=str(decoded.get("project_type", "spider")),
            priority=int(decoded.get("priority", 0) or 0),
            timeout=int(decoded.get("timeout", 3600) or 3600),
            source_bundle_uri=str(decoded.get("source_bundle_uri", "") or ""),
            source_bundle_sha256=str(decoded.get("source_bundle_sha256", "") or ""),
            source_bundle_size=int(decoded.get("source_bundle_size", 0) or 0),
            transfer_method=str(decoded.get("transfer_method", "") or ""),
            resolved_revision=str(decoded.get("resolved_revision", "") or ""),
            source_subdir=str(decoded.get("source_subdir", "") or ""),
            entry_point=str(decoded.get("entry_point", "") or ""),
            runtime_env_name=str(decoded.get("runtime_env_name", "") or ""),
            params=self._parse_json(decoded.get("params", "{}"), "params"),
            environment=self._parse_json(decoded.get("environment", "{}"), "environment"),
            trace_parent=str(decoded.get("trace_parent", "") or ""),
        )

    @staticmethod
    def _parse_json(value: object, field_name: str) -> dict[str, object]:
        if not value:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, (bytes, bytearray)):
            raw = value.decode("utf-8")
        elif isinstance(value, str):
            raw = value
        else:
            raise ValueError(f"{field_name} 必须是 JSON object")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} 不是有效 JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field_name} 必须是 JSON object")
        return parsed
