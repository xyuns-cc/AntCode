from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.infrastructure.redis as redis_module
import pytest
from antcode_worker.engine.engine import Engine


def _engine() -> Engine:
    engine = Engine.__new__(Engine)
    engine._worker_id_cache = None
    engine._transport = SimpleNamespace(_worker_id="worker-1", _lease_id="lease-7")
    return engine


@pytest.mark.asyncio
async def test_ownership_claim_fails_closed_without_redis(monkeypatch):
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="ownership claim"):
        await _engine()._claim_run_ownership("run-1")


@pytest.mark.asyncio
async def test_ownership_key_uses_configured_namespace(monkeypatch):
    redis = AsyncMock()
    redis.set.return_value = True
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(redis_module, "redis_namespace", lambda: "tenant-a")

    assert await _engine()._claim_run_ownership("run-1") is True
    redis.set.assert_awaited_once_with(
        "tenant-a:run:owner:run-1",
        "worker-1:lease-7",
        nx=True,
        ex=Engine._RUN_OWNERSHIP_TTL_SECONDS,
    )
