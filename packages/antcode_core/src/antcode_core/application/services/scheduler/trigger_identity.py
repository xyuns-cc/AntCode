"""Deterministic identity shared by trigger producers and consumers."""

from __future__ import annotations

import uuid

TRIGGER_RUN_NAMESPACE = uuid.UUID("5c1a5fd4-9a70-4a53-9d18-58f4d13ae0c2")
MANUAL_RETRY_OUTBOX_NAMESPACE = uuid.UUID("e3ba61f9-2797-4c68-835f-70cfa9bcd1b3")


def trigger_run_id(task_id: str | int, idempotency_key: str) -> str:
    if not idempotency_key:
        raise ValueError("trigger idempotency key must not be empty")
    return str(uuid.uuid5(TRIGGER_RUN_NAMESPACE, f"{task_id}:{idempotency_key}"))


def manual_retry_outbox_id(source_run_id: str) -> str:
    if not source_run_id:
        raise ValueError("manual retry source run ID must not be empty")
    return uuid.uuid5(MANUAL_RETRY_OUTBOX_NAMESPACE, source_run_id).hex


__all__ = ["MANUAL_RETRY_OUTBOX_NAMESPACE", "TRIGGER_RUN_NAMESPACE", "manual_retry_outbox_id", "trigger_run_id"]
