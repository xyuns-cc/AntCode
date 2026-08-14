"""V2 Worker registration readiness checks.

Legacy and administratively-created Workers have no V2 registration row and remain
eligible.  A Worker tied to a V2 install-key registration is eligible only after the
client has acknowledged durable receipt of its derived credentials.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from antcode_core.domain.models import Worker, WorkerInstallKey

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient


def _pending_registration_query(
    worker_public_ids: Sequence[str],
    connection: BaseDBAsyncClient | None = None,
) -> Any:
    return WorkerInstallKey.filter(
        used_by_worker__in=list(worker_public_ids),
        registration_id__isnull=False,
        registration_acknowledged_at__isnull=True,
    ).using_db(connection)


async def has_unacknowledged_v2_registration(
    worker_public_id: str,
    *,
    connection: BaseDBAsyncClient | None = None,
) -> bool:
    """Return whether ``worker_public_id`` is still inside the V2 ACK window."""
    return await _pending_registration_query([worker_public_id], connection).exists()


async def filter_registration_ready_workers(workers: Sequence[Worker]) -> list[Worker]:
    """Remove unacknowledged V2 registrations with one authoritative DB query."""
    candidates = list(workers)
    if not candidates:
        return []
    public_ids = [worker.public_id for worker in candidates]
    pending_ids = set(await _pending_registration_query(public_ids).values_list("used_by_worker", flat=True))
    return [worker for worker in candidates if worker.public_id not in pending_ids]


__all__ = [
    "filter_registration_ready_workers",
    "has_unacknowledged_v2_registration",
]
