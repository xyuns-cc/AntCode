"""Backend-neutral assertions for shared transport contracts."""

from __future__ import annotations

import json
from typing import Any

from antcode_contracts import data_pb2

from tests.contracts.proto_stream import read_proto_stream
from tests.contracts.transport_backends import REDIS_TEST_URL


class ContractProbe:
    def __init__(self, transport: Any, redis_admin: Any):
        self._transport = transport
        self._redis = redis_admin
        self._gateway = getattr(transport, "_test_gateway_state", None)

    @property
    def is_gateway(self) -> bool:
        return self._gateway is not None

    async def result_statuses(self) -> list[data_pb2.TaskStatus]:
        if self._gateway is not None:
            return list(self._gateway.statuses)
        keys = self._transport._test_keys
        return await read_proto_stream(
            REDIS_TEST_URL,
            keys.task_result_stream(),
            data_pb2.TaskStatus,
        )

    async def pending_task_count(self) -> int:
        if self._gateway is not None:
            return len(self._gateway.inflight_tasks)
        keys = self._transport._test_keys
        stream = keys.task_ready_stream(self._transport._worker_id)
        pending = await self._redis.xpending(stream, keys.consumer_group_name())
        if isinstance(pending, dict):
            return int(pending.get("pending", 0))
        return int(pending[0])

    async def advance_unacked_visibility(self, receipt: str) -> None:
        if self._gateway is not None:
            await self._gateway.advance_unacked_visibility(
                receipt,
                self._gateway.visibility_timeout_ms + 1,
            )
            return
        from antcode_worker.transport.redis.reclaim import ReclaimConfig

        stream, message_id = receipt.split("|", 1)
        threshold = ReclaimConfig().min_idle_time_ms
        expired_consumer = f"{self._transport._task_consumer_name}-expired"
        claimed = await self._redis.xclaim(
            stream,
            self._transport._consumer_group,
            expired_consumer,
            min_idle_time=0,
            message_ids=[message_id],
            idle=threshold + 1,
            justid=True,
        )
        assert message_id in claimed

    async def trigger_unacked_reclaim(self) -> None:
        if self._gateway is not None:
            return
        reclaimer = self._transport._reclaimer
        assert reclaimer is not None
        reclaimed = await reclaimer.reclaim_once()
        assert len(reclaimed) == 1
        task = reclaimed[0]
        await self._transport._enqueue_reclaimed(task.message_id, task.data)

    async def log_batches(self) -> list[data_pb2.LogBatch]:
        if self._gateway is not None:
            return list(self._gateway.log_batches)
        keys = self._transport._test_keys
        return await read_proto_stream(
            REDIS_TEST_URL,
            keys.log_ingest_stream(),
            data_pb2.LogBatch,
        )

    async def no_log_streams(self, run_id: str) -> bool:
        if self._gateway is not None:
            return not self._gateway.log_batches
        keys = self._transport._test_keys
        chunk_exists = await self._redis.exists(keys.log_chunk_stream(run_id))
        ingest_exists = await self._redis.exists(keys.log_ingest_stream())
        return chunk_exists == 0 and ingest_exists == 0

    async def heartbeat_fields(self) -> dict[str, str]:
        if self._gateway is not None:
            request = self._gateway.lease_requests[-1]
            return {
                "cpu_percent": str(request.metrics.cpu),
                "running_tasks": str(request.metrics.running_tasks),
            }
        keys = self._transport._test_keys
        worker_id = self._transport._worker_id
        return await self._redis.hgetall(keys.heartbeat_key(worker_id))

    async def heartbeat_ttl(self) -> int:
        if self._gateway is not None:
            return 30 if self._gateway.lease_requests else -2
        keys = self._transport._test_keys
        worker_id = self._transport._worker_id
        return await self._redis.ttl(keys.heartbeat_key(worker_id))

    async def push_control(
        self,
        *,
        control_type: str,
        task_id: str,
        run_id: str,
        reason: str,
    ) -> str:
        if self._gateway is not None:
            return await self._gateway.enqueue_control(
                control_type=control_type,
                task_id=task_id,
                run_id=run_id,
                reason=reason,
            )
        keys = self._transport._test_keys
        stream = keys.control_stream(self._transport._worker_id)
        return await self._redis.xadd(
            stream,
            {
                "control_type": control_type,
                "task_id": task_id,
                "run_id": run_id,
                "reason": reason,
            },
        )

    async def push_runtime_control(
        self,
        *,
        request_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> str:
        if self._gateway is not None:
            return await self._gateway.enqueue_runtime_control(
                request_id=request_id,
                action=action,
                payload=payload,
            )
        reply_stream = self.reply_stream(request_id)
        keys = self._transport._test_keys
        stream = keys.control_stream(self._transport._worker_id)
        return await self._redis.xadd(
            stream,
            {
                "control_type": "runtime_manage",
                "request_id": request_id,
                "action": action,
                "payload": json.dumps(payload, ensure_ascii=False),
                "reply_stream": reply_stream,
            },
        )

    def reply_stream(self, request_id: str) -> str:
        namespace = getattr(self._transport, "_test_namespace", "gateway")
        return f"{namespace}:control:reply:{request_id}"

    async def control_result(self, request_id: str) -> dict[str, str]:
        if self._gateway is not None:
            matching = [request for request in self._gateway.control_acks if request.request_id == request_id]
            assert len(matching) == 1
            request = matching[0]
            return {
                "request_id": request.request_id,
                "success": "true" if request.success else "false",
                "error": request.error,
                "data": request.data_json,
            }
        entries = await self._redis.xrange(self.reply_stream(request_id), count=5)
        assert len(entries) == 1
        _message_id, fields = entries[0]
        return fields
