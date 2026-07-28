"""In-process gRPC Gateway used by transport contract tests."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import grpc
from antcode_contracts import (
    artifact_pb2_grpc,
    control_pb2,
    control_pb2_grpc,
    data_pb2,
    data_pb2_grpc,
)

from tests.contracts.fake_gateway_artifact import FakeArtifactService

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
STREAM_CLOSE_TIMEOUT_SECONDS = 2.0
RUNTIME_CONTROL_TTL_MS = 30_000


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(item, (dict, list)):
            result[str(key)] = json.dumps(item, ensure_ascii=False)
        elif isinstance(item, bool):
            result[str(key)] = "true" if item else "false"
        else:
            result[str(key)] = str(item)
    return result


def _copy_message(message: Any) -> Any:
    copied = type(message)()
    copied.CopyFrom(message)
    return copied


@dataclass
class FakeGatewayState:
    """Observable state shared by the fake services and test probes."""

    worker_id: str
    visibility_timeout_ms: int
    task_queue: asyncio.Queue[data_pb2.TaskDispatch] = field(default_factory=asyncio.Queue)
    control_queue: asyncio.Queue[control_pb2.ControlEvent] = field(default_factory=asyncio.Queue)
    inflight_tasks: dict[str, data_pb2.TaskDispatch] = field(default_factory=dict)
    statuses: list[data_pb2.TaskStatus] = field(default_factory=list)
    log_batches: list[data_pb2.LogBatch] = field(default_factory=list)
    lease_requests: list[control_pb2.LeaseRequest] = field(default_factory=list)
    control_acks: list[control_pb2.AckControlRequest] = field(default_factory=list)
    task_streams_closed: asyncio.Event = field(default_factory=asyncio.Event)
    active_task_streams: int = 0
    run_owners: dict[str, tuple[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.task_streams_closed.set()

    def task_stream_started(self) -> None:
        self.active_task_streams += 1
        self.task_streams_closed.clear()

    def task_stream_finished(self) -> None:
        self.active_task_streams -= 1
        if self.active_task_streams == 0:
            self.task_streams_closed.set()

    def _receipt_id(self) -> str:
        return f"fake:task:{self.worker_id}|{uuid.uuid4().hex}"

    async def enqueue_task(self, payload: dict[str, Any]) -> str:
        digest = str(payload.get("source_bundle_sha256") or _EMPTY_SHA256)
        receipt_id = self._receipt_id()
        dispatch = data_pb2.TaskDispatch(
            task_id=str(payload.get("task_id", "")),
            project_id=str(payload.get("project_id", "")),
            project_type=str(payload.get("project_type", "code")),
            priority=int(payload.get("priority", 0) or 0),
            params=_string_map(payload.get("params")),
            environment=_string_map(payload.get("environment")),
            timeout_seconds=int(payload.get("timeout_seconds", payload.get("timeout", 3600)) or 3600),
            source_bundle_uri=str(payload.get("source_bundle_uri") or f"pgartifact://{digest}"),
            source_bundle_sha256=digest,
            source_bundle_size=int(payload.get("source_bundle_size", 0) or 0),
            transfer_method=str(payload.get("transfer_method", "source_bundle")),
            resolved_revision=str(payload.get("resolved_revision", "contract-test")),
            source_subdir=str(payload.get("source_subdir", "")),
            entry_point=str(payload.get("entry_point", "")),
            run_id=str(payload.get("run_id", "")),
            receipt_id=receipt_id,
            runtime_env_name=str(payload.get("runtime_env_name", "")),
        )
        await self.task_queue.put(dispatch)
        return receipt_id

    async def acknowledge_task(self, receipt_id: str, accepted: bool) -> bool:
        dispatch = self.inflight_tasks.pop(receipt_id, None)
        if dispatch is None:
            return False
        if not accepted:
            requeued = _copy_message(dispatch)
            requeued.receipt_id = self._receipt_id()
            await self.task_queue.put(requeued)
        return True

    async def advance_unacked_visibility(self, receipt_id: str, elapsed_ms: int) -> None:
        if elapsed_ms < self.visibility_timeout_ms:
            raise ValueError("visibility timeout has not elapsed")
        await asyncio.wait_for(
            self.task_streams_closed.wait(),
            timeout=STREAM_CLOSE_TIMEOUT_SECONDS,
        )
        dispatch = self.inflight_tasks.pop(receipt_id, None)
        if dispatch is None:
            raise AssertionError(f"unknown in-flight receipt: {receipt_id}")
        await self.task_queue.put(dispatch)

    async def enqueue_control(
        self,
        *,
        control_type: str,
        task_id: str,
        run_id: str,
        reason: str,
    ) -> str:
        event_id = f"fake:control:{self.worker_id}|{uuid.uuid4().hex}"
        event = control_pb2.ControlEvent(event_id=event_id)
        if control_type in {"cancel", "kill"}:
            event.task_cancel.CopyFrom(control_pb2.TaskCancel(task_id=task_id, run_id=run_id, reason=reason))
        else:
            event.config_update.config["control_type"] = control_type
        await self.control_queue.put(event)
        return event_id

    async def enqueue_runtime_control(
        self,
        *,
        request_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> str:
        event_id = f"fake:control:{self.worker_id}|{uuid.uuid4().hex}"
        event = control_pb2.ControlEvent(event_id=event_id)
        observed_at_ms = int(time.time() * 1000)
        event.runtime_control.CopyFrom(
            control_pb2.RuntimeControl(
                request_id=request_id,
                action=action,
                params={"reply_stream": f"gateway:control:reply:{request_id}"},
                expires_at_ms=observed_at_ms + RUNTIME_CONTROL_TTL_MS,
                gateway_observed_at_ms=observed_at_ms,
                action_typed=control_pb2.RuntimeAction(
                    generic=control_pb2.GenericAction(name=action, args=_string_map(payload))
                ),
            )
        )
        await self.control_queue.put(event)
        return event_id


class FakeDataService(data_pb2_grpc.DataServiceServicer):
    def __init__(self, state: FakeGatewayState):
        self._state = state

    async def StreamTasks(
        self,
        request: data_pb2.SubscribeRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[data_pb2.TaskDispatch]:
        await context.send_initial_metadata(())
        self._state.task_stream_started()
        try:
            while not context.cancelled():
                dispatch = await self._state.task_queue.get()
                if context.cancelled():
                    await self._state.task_queue.put(dispatch)
                    return
                self._state.inflight_tasks[dispatch.receipt_id] = dispatch
                yield dispatch
        finally:
            self._state.task_stream_finished()

    async def AckTask(self, request, context):
        success = await self._state.acknowledge_task(
            request.receipt_id,
            bool(request.accepted),
        )
        return data_pb2.AckTaskResponse(
            success=success,
            error="" if success else "unknown receipt",
        )

    async def StreamStatus(self, request_iterator, context):
        received = 0
        async for status in request_iterator:
            self._state.statuses.append(_copy_message(status))
            received += 1
        return data_pb2.StatusAck(received=received)

    async def StreamLogs(self, request_iterator, context):
        received = 0
        async for batch in request_iterator:
            self._state.log_batches.append(_copy_message(batch))
            received += len(batch.entries)
        return data_pb2.LogAck(received=received)


class FakeControlService(control_pb2_grpc.ControlServiceServicer):
    def __init__(self, state: FakeGatewayState):
        self._state = state

    async def Register(self, request, context):
        return control_pb2.RegisterResponse(
            success=True,
            worker_id=request.worker_id,
            lease_ttl_ms=30_000,
            lease_renew_after_ms=10_000,
            runtime_control_results_v1=True,
        )

    async def GetCapabilities(self, request, context):
        return control_pb2.CapabilitiesResponse(
            runtime_control_results_v1=True,
            runtime_control_lease_fencing_v1=True,
            runtime_control_deadline_v1=True,
            artifact_transfer_v1=True,
            runtime_control_authoritative_clock_v1=True,
            worker_capabilities_v1=True,
        )

    async def Lease(self, request, context):
        self._state.lease_requests.append(_copy_message(request))
        lease_id = request.current_lease_id or f"lease-{self._state.worker_id}"
        return control_pb2.LeaseResponse(
            lease_id=lease_id,
            expires_at_ms=int(time.time() * 1000) + 30_000,
            renew_after_ms=10_000,
        )

    async def WatchControl(
        self,
        request: control_pb2.WatchControlRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[control_pb2.ControlEvent]:
        await context.send_initial_metadata(())
        while True:
            yield await self._state.control_queue.get()

    async def AckControl(self, request, context):
        self._state.control_acks.append(_copy_message(request))
        return control_pb2.AckControlResponse(received=True)


@dataclass(frozen=True)
class RunningFakeGateway:
    state: FakeGatewayState
    host: str
    port: int


@asynccontextmanager
async def run_fake_gateway(
    worker_id: str,
    visibility_timeout_ms: int,
) -> AsyncIterator[RunningFakeGateway]:
    state = FakeGatewayState(
        worker_id=worker_id,
        visibility_timeout_ms=visibility_timeout_ms,
    )
    server = grpc.aio.server()
    data_pb2_grpc.add_DataServiceServicer_to_server(FakeDataService(state), server)
    control_pb2_grpc.add_ControlServiceServicer_to_server(FakeControlService(state), server)
    artifact_pb2_grpc.add_ArtifactServiceServicer_to_server(FakeArtifactService(state), server)
    host = "127.0.0.1"
    port = server.add_insecure_port(f"{host}:0")
    if port == 0:
        raise RuntimeError("fake Gateway failed to bind an ephemeral port")
    await server.start()
    try:
        yield RunningFakeGateway(state=state, host=host, port=port)
    finally:
        await server.stop(grace=0)
