"""Persisted Worker credential retirement after lease fencing."""

from __future__ import annotations

from typing import Any

WORKER_CREDENTIAL_FIELDS = (
    "api_key_hash",
    "api_key_previous_hash",
    "api_key_previous_expires_at",
    "secret_key_hash",
    "secret_key_encrypted",
)


def clear_worker_credentials(worker: Any) -> None:
    for field in WORKER_CREDENTIAL_FIELDS:
        setattr(worker, field, None)


async def persist_cleared_worker_credentials(worker: Any) -> None:
    clear_worker_credentials(worker)
    await worker.save(update_fields=list(WORKER_CREDENTIAL_FIELDS))


__all__ = ["WORKER_CREDENTIAL_FIELDS", "clear_worker_credentials", "persist_cleared_worker_credentials"]
