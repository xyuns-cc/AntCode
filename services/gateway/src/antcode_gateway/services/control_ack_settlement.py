"""Validate and atomically settle Gateway AckControl requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from antcode_contracts import control_pb2
from loguru import logger

from antcode_gateway.services.control_stream_ownership import (
    ControlAckOutcome,
    ControlPendingEntry,
    ack_owned_control_entry,
    control_consumer_name,
)
from antcode_gateway.services.runtime_control_result import (
    ControlEventGoneError,
    is_committed_runtime_result,
    publish_runtime_control_result,
    require_pending_owner,
    validate_control_ack,
)


@dataclass(frozen=True)
class _ControlSettlement:
    event_id: str
    stream_key: str
    message_id: str
    worker_id: str
    group: str
    request: control_pb2.AckControlRequest

    @property
    def consumer_name(self) -> str:
        return control_consumer_name(self.worker_id, self.request.lease_id)

    @property
    def pending_entry(self) -> ControlPendingEntry:
        return ControlPendingEntry(
            self.worker_id,
            self.request.lease_id,
            self.stream_key,
            self.group,
            self.message_id,
            self.consumer_name,
        )


async def settle_control_ack(
    redis: Any,
    *,
    event_id: str,
    stream_key: str,
    message_id: str,
    worker_id: str,
    group: str,
    request: control_pb2.AckControlRequest,
) -> bool:
    settlement = _ControlSettlement(
        event_id,
        stream_key,
        message_id,
        worker_id,
        group,
        request,
    )
    return await _settle(redis, settlement)


async def _settle(redis: Any, settlement: _ControlSettlement) -> bool:
    committed = await is_committed_runtime_result(
        redis,
        event_id=settlement.event_id,
        worker_id=settlement.worker_id,
        request=settlement.request,
    )
    if not committed:
        try:
            decoded, is_runtime = await _validate_pending(redis, settlement)
        except ControlEventGoneError as exc:
            _log_idempotent_replay(settlement, exc)
            return False
        if is_runtime:
            await _publish_runtime_result(redis, settlement, decoded)
            committed = True
    outcome = await ack_owned_control_entry(redis, settlement.pending_entry)
    return committed or outcome is ControlAckOutcome.ACKED


async def _validate_pending(
    redis: Any,
    settlement: _ControlSettlement,
) -> tuple[dict[str, Any], bool]:
    decoded, is_runtime = await validate_control_ack(
        redis,
        stream_key=settlement.stream_key,
        message_id=settlement.message_id,
        request=settlement.request,
    )
    await require_pending_owner(
        redis,
        stream_key=settlement.stream_key,
        group=settlement.group,
        message_id=settlement.message_id,
        consumer_name=settlement.consumer_name,
    )
    return decoded, is_runtime


async def _publish_runtime_result(
    redis: Any,
    settlement: _ControlSettlement,
    decoded: dict[str, Any],
) -> None:
    await publish_runtime_control_result(
        redis,
        event_id=settlement.event_id,
        worker_id=settlement.worker_id,
        message_id=settlement.message_id,
        decoded=decoded,
        request=settlement.request,
    )


def _log_idempotent_replay(
    settlement: _ControlSettlement,
    error: ControlEventGoneError,
) -> None:
    logger.info(
        "AckControl 幂等重放（事件已结算）: worker={} stream={} msg_id={} detail={}",
        settlement.worker_id,
        settlement.stream_key,
        settlement.message_id,
        error,
    )


__all__ = ["settle_control_ack"]
