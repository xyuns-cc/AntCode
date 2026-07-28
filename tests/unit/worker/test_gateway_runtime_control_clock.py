"""Gateway Worker runtime-control 权威时钟回归。"""

from collections.abc import Iterator

import pytest
from antcode_contracts import control_pb2
from antcode_worker.transport.gateway.codecs import ControlDecoder
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport

GATEWAY_OBSERVED_AT_MS = 100_000
RUNTIME_DEADLINE_MS = 130_000
LOCAL_RECEIVED_AT_NS = 130_000_000_000
LOCAL_CHECKED_AT_NS = 135_000_000_000
EXPECTED_AUTHORITY_NOW_MS = 105_000


@pytest.mark.asyncio
async def test_gateway_redis_time_survives_worker_wall_clock_skew(monkeypatch) -> None:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    event = _runtime_event(gateway_observed_at_ms=GATEWAY_OBSERVED_AT_MS)
    monotonic_times = iter((LOCAL_RECEIVED_AT_NS, LOCAL_CHECKED_AT_NS))
    _patch_monotonic_clock(monkeypatch, monotonic_times)

    message = transport._control_event_to_message(event, ControlDecoder)

    assert message is not None
    assert message.payload["expires_at_ms"] == RUNTIME_DEADLINE_MS
    assert await transport.authoritative_now_ms() == EXPECTED_AUTHORITY_NOW_MS


def test_runtime_control_without_gateway_redis_time_is_rejected() -> None:
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))

    with pytest.raises(RuntimeError, match="缺少 Redis 权威时钟观测值"):
        transport._control_event_to_message(_runtime_event(), ControlDecoder)


def _runtime_event(*, gateway_observed_at_ms: int = 0) -> control_pb2.ControlEvent:
    return control_pb2.ControlEvent(
        event_id="antcode:control:worker-1|1-0",
        runtime_control=control_pb2.RuntimeControl(
            request_id="request-1",
            action="list_envs",
            expires_at_ms=RUNTIME_DEADLINE_MS,
            gateway_observed_at_ms=gateway_observed_at_ms,
        ),
    )


def _patch_monotonic_clock(monkeypatch, values: Iterator[int]) -> None:
    monkeypatch.setattr(
        "antcode_worker.transport.gateway.runtime_control_clock.time.monotonic_ns",
        lambda: next(values),
    )
