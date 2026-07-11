from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.infrastructure.redis as redis_module
import pytest
from antcode_core.application.services.workers.worker_heartbeat_service import (
    WorkerHeartbeatService,
)


class FakeRedis:
    def __init__(self, raw=None, error: Exception | None = None):
        self.raw = raw or {}
        self.error = error

    async def hgetall(self, key):
        if self.error:
            raise self.error
        return self.raw


def _worker(**overrides):
    defaults = {
        "id": 1,
        "public_id": "worker-1",
        "name": "Worker-1",
        "last_heartbeat": None,
        "metrics": {},
        "status": "offline",
        "version": "",
        "os_type": "",
        "os_version": "",
        "python_version": "",
        "machine_arch": "",
        "capabilities": {},
        "save": AsyncMock(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_get_redis_heartbeat_exposes_redis_errors(monkeypatch):
    service = WorkerHeartbeatService()
    monkeypatch.setattr(
        redis_module,
        "get_redis_client",
        AsyncMock(return_value=FakeRedis(error=RuntimeError("redis unavailable"))),
    )

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await service._get_redis_heartbeat(_worker())


@pytest.mark.asyncio
async def test_get_redis_heartbeat_rejects_invalid_timestamp(monkeypatch):
    service = WorkerHeartbeatService()
    monkeypatch.setattr(
        redis_module,
        "get_redis_client",
        AsyncMock(return_value=FakeRedis({"timestamp": "not-a-date"})),
    )

    with pytest.raises(ValueError, match="not-a-date"):
        await service._get_redis_heartbeat(_worker())


@pytest.mark.asyncio
async def test_sync_redis_heartbeat_exposes_db_save_errors(monkeypatch):
    service = WorkerHeartbeatService()
    worker = _worker(save=AsyncMock(side_effect=RuntimeError("postgres unavailable")))
    monkeypatch.setattr(
        redis_module,
        "get_redis_client",
        AsyncMock(return_value=FakeRedis({"timestamp": datetime.now().isoformat()})),
    )

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await service._sync_redis_heartbeat_to_db(worker)
