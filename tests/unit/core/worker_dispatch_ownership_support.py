"""Shared fakes for dispatch ownership tests."""

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot

WORKER_ID = 7
LEASE_ID = "lease-7"
LEASE_GEN = 7
SCOPE_TASK_ID = 17
TWO_RUNS = 2


class Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class WorkerQuery:
    def __init__(self, worker):
        self._worker = worker
        self.locked = False

    def using_db(self, _conn):
        return self

    def select_for_update(self):
        self.locked = True
        return self

    async def first(self):
        return self._worker


class RunQuery:
    def __init__(self, *, update_result=0, rows=None):
        self.update = AsyncMock(return_value=update_result)
        self._rows = rows or []
        self.exclude_kwargs: dict | None = None

    def exclude(self, **kwargs):
        self.exclude_kwargs = kwargs
        return self

    def using_db(self, _conn):
        return self

    def only(self, *_fields):
        return self

    async def all(self):
        return self._rows


class TaskQuery:
    def __init__(self, exists=True):
        self.exists = AsyncMock(return_value=exists)

    def using_db(self, _conn):
        return self


def scope_task() -> dict:
    return {
        "task_id": SCOPE_TASK_ID,
        "run_id": "run-17",
        "_dispatch_scope": {
            "run_id": "run-17",
            "task_id": SCOPE_TASK_ID,
            "project_id": 9,
            "owner_id": 3,
        },
    }


def snapshot() -> LeaseCapabilitySnapshot:
    return LeaseCapabilitySnapshot(LEASE_ID, '{"task_types":["rule"]}', LEASE_GEN)


def online_worker_query() -> WorkerQuery:
    return WorkerQuery(SimpleNamespace(id=WORKER_ID, status="online"))
