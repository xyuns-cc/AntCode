"""GatewayTransport 现代化行为测试。"""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2, data_pb2
from antcode_worker.transport.base import LogMessage
from antcode_worker.transport.gateway.codecs import CodecError, ControlDecoder
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


class LogStreamStub:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.batches = []

    async def StreamLogs(self, request_iterator, metadata):
        if self.error is not None:
            raise self.error
        self.batches = [batch async for batch in request_iterator]
        received = sum(len(batch.entries) for batch in self.batches)
        return data_pb2.LogAck(received=received)


class InvalidArgumentError(RuntimeError):
    def code(self):
        return grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_protocol_check_requires_runtime_result_capability():
    stub = MagicMock(
        GetCapabilities=AsyncMock(
            return_value=control_pb2.CapabilitiesResponse(
                runtime_control_results_v1=False,
            )
        )
    )
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1", api_key="api-key"))
    transport._control_stub = stub

    with pytest.raises(RuntimeError, match="不支持 runtime_control_results_v1"):
        await transport._check_protocol_capabilities()


@pytest.mark.asyncio
async def test_protocol_check_requires_runtime_control_lease_fencing():
    stub = MagicMock(
        GetCapabilities=AsyncMock(
            return_value=control_pb2.CapabilitiesResponse(
                runtime_control_results_v1=True,
                runtime_control_lease_fencing_v1=False,
                runtime_control_deadline_v1=True,
                artifact_transfer_v1=True,
            )
        )
    )
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._control_stub = stub

    with pytest.raises(RuntimeError, match="runtime_control_lease_fencing_v1"):
        await transport._check_protocol_capabilities()


@pytest.mark.asyncio
async def test_protocol_check_requires_runtime_control_deadlines():
    stub = MagicMock(
        GetCapabilities=AsyncMock(
            return_value=control_pb2.CapabilitiesResponse(
                runtime_control_results_v1=True,
                runtime_control_lease_fencing_v1=True,
                runtime_control_deadline_v1=False,
                artifact_transfer_v1=True,
            )
        )
    )
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._control_stub = stub

    with pytest.raises(RuntimeError, match="runtime_control_deadline_v1"):
        await transport._check_protocol_capabilities()


@pytest.mark.asyncio
async def test_protocol_check_requires_artifact_transfer():
    response = control_pb2.CapabilitiesResponse(
        runtime_control_results_v1=True,
        runtime_control_lease_fencing_v1=True,
        runtime_control_deadline_v1=True,
        artifact_transfer_v1=False,
    )
    stub = MagicMock(GetCapabilities=AsyncMock(return_value=response))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._control_stub = stub

    with pytest.raises(RuntimeError, match="artifact_transfer_v1"):
        await transport._check_protocol_capabilities()


@pytest.mark.asyncio
async def test_protocol_check_requires_authoritative_runtime_clock():
    response = control_pb2.CapabilitiesResponse(
        runtime_control_results_v1=True,
        runtime_control_lease_fencing_v1=True,
        runtime_control_deadline_v1=True,
        artifact_transfer_v1=True,
        runtime_control_authoritative_clock_v1=False,
    )
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._control_stub = MagicMock(GetCapabilities=AsyncMock(return_value=response))

    with pytest.raises(RuntimeError, match="runtime_control_authoritative_clock_v1"):
        await transport._check_protocol_capabilities()


@pytest.mark.asyncio
async def test_connect_closes_channel_when_capability_check_fails(monkeypatch):
    channel = MagicMock(channel_ready=AsyncMock(), close=AsyncMock())
    stub = MagicMock(
        GetCapabilities=AsyncMock(
            return_value=control_pb2.CapabilitiesResponse(
                runtime_control_results_v1=False,
            )
        )
    )
    monkeypatch.setattr(grpc.aio, "insecure_channel", MagicMock(return_value=channel))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._create_stubs = MagicMock(return_value=(stub, MagicMock(), MagicMock()))

    assert await transport._connect() is False
    channel.close.assert_awaited_once()
    assert transport._channel is None


def test_gateway_config_loads_shared_log_limits_from_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOG_MAX_BATCH_BYTES", "2048")
    monkeypatch.setenv("GATEWAY_LOG_MAX_ENTRY_CONTENT_BYTES", "512")

    config = GatewayConfig()

    assert config.log_max_batch_bytes == 2048
    assert config.log_max_entry_content_bytes == 512


def test_gateway_config_rejects_non_positive_log_limit(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOG_MAX_BATCH_BYTES", "0")

    with pytest.raises(ValueError, match="GATEWAY_LOG_MAX_BATCH_BYTES"):
        GatewayConfig()


@pytest.mark.asyncio
async def test_poll_task_exposes_decoder_protocol_errors():
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id="worker-1",
        ),
    )
    transport._running = True
    transport._task_inbox = asyncio.Queue()
    await transport._task_inbox.put(CodecError("source_bundle_uri must be a pgartifact:// URI"))

    with pytest.raises(CodecError, match="pgartifact"):
        await transport.poll_task(timeout=0.1)


@pytest.mark.asyncio
async def test_poll_control_cancel_maps_to_run_id():
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id="worker-1",
        ),
    )
    transport._running = True
    transport._control_inbox = asyncio.Queue()
    event = MagicMock()
    event.event_id = "event-1"
    event.WhichOneof.return_value = "task_cancel"
    event.task_cancel.task_id = "task-1"
    event.task_cancel.run_id = "run-1"
    event.task_cancel.reason = "cancelled"
    message = transport._control_event_to_message(event, ControlDecoder)
    await transport._control_inbox.put(message)

    message = await transport.poll_control(timeout=0.1)

    assert message is not None
    assert message.control_type == "cancel"
    assert message.task_id == "task-1"
    assert message.run_id == "run-1"


@pytest.mark.asyncio
async def test_defer_task_does_not_send_reject_ack_to_gateway():
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id="worker-1",
        ),
    )
    receipt = "{antcode}:task:ready:worker-1|1-0"
    transport._receipt_cache[receipt] = (0.0, "task-1")
    transport._data_stub = MagicMock()

    assert await transport.defer_task(receipt, reason="ownership_contention run_id=run-1") is True

    assert transport._data_stub.method_calls == []
    assert receipt not in transport._receipt_cache


def _log(sequence: int, content: str = "line") -> LogMessage:
    return LogMessage(
        run_id="run-1",
        log_type="stdout",
        content=content,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_send_log_batch_splits_on_encoded_protobuf_budget():
    # 预算需覆盖 batch 元数据（worker/lease/trace + 64 字符 batch_id 占位）。
    batch_budget = 260
    stub = LogStreamStub()
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            worker_id="worker-1",
            log_max_batch_bytes=batch_budget,
            log_max_entry_content_bytes=100,
        ),
    )
    transport._running = True
    transport._data_stub = stub
    transport._lease_id = "lease-1"

    assert await transport.send_log_batch([_log(1, "a" * 40), _log(2, "b" * 40)]) is True

    assert len(stub.batches) == 2
    assert sum(len(batch.entries) for batch in stub.batches) == 2
    # P1-GW-03: 最终序列化大小（含真实 batch_id）仍必须 ≤ 预算。
    assert all(batch.ByteSize() <= batch_budget for batch in stub.batches)
    assert all(batch.lease_id == "lease-1" for batch in stub.batches)
    assert all(len(batch.batch_id) == 64 for batch in stub.batches)
    assert stub.batches[0].batch_id != stub.batches[1].batch_id


@pytest.mark.asyncio
async def test_send_log_batch_exposes_single_entry_content_overflow():
    stub = MagicMock(StreamLogs=AsyncMock())
    transport = GatewayTransport(
        gateway_config=GatewayConfig(
            worker_id="worker-1",
            log_max_entry_content_bytes=2,
        ),
    )
    transport._running = True
    transport._data_stub = stub
    transport._lease_id = "lease-1"

    with pytest.raises(ValueError, match="LogEntry content bytes 超限"):
        await transport.send_log(_log(1, "你"))

    stub.StreamLogs.assert_not_called()


@pytest.mark.asyncio
async def test_send_log_batch_exposes_gateway_invalid_argument():
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._data_stub = LogStreamStub(InvalidArgumentError("invalid log batch"))
    transport._lease_id = "lease-1"

    with pytest.raises(ValueError, match="Gateway 拒绝日志批次"):
        await transport.send_log(_log(1))


def test_subscribe_request_carries_current_lease():
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._data_stub = MagicMock()
    transport._lease_id = "lease-1"

    request = transport._build_subscribe_request()

    assert request.worker_id == "worker-1"
    assert request.lease_id == "lease-1"


@pytest.mark.asyncio
async def test_send_control_result_carries_receipt_and_structured_data():
    stub = MagicMock(AckControl=AsyncMock(return_value=control_pb2.AckControlResponse(received=True)))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._lease_id = "lease-1"
    transport._running = True
    transport._control_stub = stub

    sent = await transport.send_control_result(
        "request-1",
        "ignored-reply-stream",
        True,
        receipt="antcode:control:worker-1|1-0",
        data={"envs": ["shared-py312"]},
    )

    assert sent is True
    request = stub.AckControl.await_args.args[0]
    assert request.event_id == "antcode:control:worker-1|1-0"
    assert request.request_id == "request-1"
    assert request.lease_id == "lease-1"
    assert json.loads(request.data_json) == {"envs": ["shared-py312"]}


def test_watch_control_request_carries_current_lease():
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._lease_id = "lease-1"

    request = transport._build_watch_control_request()

    assert request.worker_id == "worker-1"
    assert request.lease_id == "lease-1"


@pytest.mark.asyncio
async def test_send_control_result_requires_receipt():
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = MagicMock()

    with pytest.raises(ValueError, match="缺少原控制事件 receipt"):
        await transport.send_control_result("request-1", "ignored", True, data=None)


@pytest.mark.asyncio
async def test_send_control_result_encodes_none_as_json_null():
    stub = MagicMock(AckControl=AsyncMock(return_value=control_pb2.AckControlResponse(received=True)))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = stub

    await transport.send_control_result(
        "request-1",
        "ignored",
        False,
        receipt="antcode:control:worker-1|1-0",
        data=None,
        error="failed",
    )

    request = stub.AckControl.await_args.args[0]
    assert request.data_json == "null"


@pytest.mark.asyncio
async def test_send_control_result_exposes_permanent_gateway_rejection():
    stub = MagicMock(AckControl=AsyncMock(side_effect=InvalidArgumentError("invalid settlement")))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = stub

    with pytest.raises(RuntimeError, match="永久拒绝运行时控制结果"):
        await transport.send_control_result(
            "request-1",
            "ignored",
            True,
            receipt="antcode:control:worker-1|1-0",
            data={},
        )


@pytest.mark.asyncio
async def test_send_control_result_degrades_unserializable_data_without_connection_error():
    """W2 回归：data 不可 JSON 序列化时降级为失败结算，不走连接错误处理。"""
    stub = MagicMock(AckControl=AsyncMock(return_value=control_pb2.AckControlResponse(received=True)))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = stub
    transport._handle_connection_error = AsyncMock()

    sent = await transport.send_control_result(
        "request-1",
        "ignored",
        True,
        receipt="antcode:control:worker-1|1-0",
        data={"ts": datetime(2026, 1, 1)},  # datetime 无法 JSON 序列化
    )

    assert sent is True
    transport._handle_connection_error.assert_not_awaited()
    assert transport._consecutive_failures == 0
    request = stub.AckControl.await_args.args[0]
    assert request.success is False
    assert "结果序列化失败" in request.error
    assert request.data_json == "null"


@pytest.mark.asyncio
async def test_unknown_control_payload_is_acked_without_poisoning_inbox():
    """W3 回归：未知 ControlEvent payload 直接 ACK 跳过，inbox 不收异常。"""
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_inbox = asyncio.Queue()
    transport.ack_control = AsyncMock(return_value=True)
    event = MagicMock(event_id="event-unknown")
    event.WhichOneof.return_value = "future_payload_kind"

    await transport._deliver_control_event(event, ControlDecoder)

    transport.ack_control.assert_awaited_once_with("event-unknown")
    assert transport._control_inbox.empty()
