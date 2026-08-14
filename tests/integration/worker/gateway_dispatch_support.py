"""Encrypted Gateway dispatch helpers for Worker integration tests."""

from __future__ import annotations

import json
from typing import Any

from antcode_contracts import data_pb2
from antcode_core.common.security.task_payload_envelope import seal_ready_payload
from antcode_worker.transport.base import TaskMessage
from antcode_worker.transport.gateway.codecs import TaskDecoder

GATEWAY_TASK_PAYLOAD_SECRET = "gateway-integration-task-secret-material-0001"


def make_gateway_dispatch(*, worker_id: str, **payload: Any) -> data_pb2.TaskDispatch:
    sealed = seal_ready_payload(
        payload,
        worker_id=worker_id,
        worker_secret=GATEWAY_TASK_PAYLOAD_SECRET,
    )
    return data_pb2.TaskDispatch(
        task_id=str(payload.get("task_id", "")),
        receipt_id="integration-receipt",
        sealed_ready_payload=json.dumps(
            sealed,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode(),
    )


def decode_gateway_dispatch(dispatch: data_pb2.TaskDispatch, *, worker_id: str) -> TaskMessage:
    return TaskDecoder.decode(
        dispatch,
        worker_id=worker_id,
        worker_secret=GATEWAY_TASK_PAYLOAD_SECRET,
    )


__all__ = ["decode_gateway_dispatch", "make_gateway_dispatch"]
