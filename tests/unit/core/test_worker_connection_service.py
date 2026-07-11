from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.worker_connection_service import WorkerConnectionService
from antcode_core.domain.models import Worker, WorkerStatus
from antcode_core.domain.schemas.worker import WorkerCapabilities, WorkerRegisterDirectRequest


class _Query:
    def __init__(self, worker, filters: dict) -> None:
        self._worker = worker
        self._filters = filters

    async def first(self):
        return self._worker if "public_id" in self._filters else None

    def exclude(self, **_kwargs):
        return self

    async def exists(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_direct_reregistration_preserves_maintenance_and_updates_capabilities(monkeypatch) -> None:
    async def save() -> None:
        return None

    worker = SimpleNamespace(
        id=7,
        name="worker-old",
        status=WorkerStatus.MAINTENANCE.value,
        capabilities={},
        save=save,
    )
    monkeypatch.setattr(Worker, "filter", lambda **filters: _Query(worker, filters))
    request = WorkerRegisterDirectRequest(
        worker_id="worker-1",
        proof="proof",
        name="worker-new",
        host="127.0.0.1",
        port=8001,
        region="cn",
        capabilities=WorkerCapabilities(task_types=["code"]),
    )

    result, created = await WorkerConnectionService().register_direct_worker(request)

    assert result is worker
    assert created is False
    assert worker.status == WorkerStatus.MAINTENANCE.value
    assert worker.name == "worker-new"
    assert worker.capabilities["task_types"] == ["code"]
