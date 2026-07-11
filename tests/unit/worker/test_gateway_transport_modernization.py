"""GatewayTransport 现代化行为测试。"""

import asyncio
from unittest.mock import MagicMock

import pytest
from antcode_worker.transport.gateway.codecs import CodecError, ControlDecoder
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport


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
