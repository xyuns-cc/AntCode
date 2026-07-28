from types import SimpleNamespace

import pytest
from antcode_master.ingester.worker_registration_cleanup_loop import WorkerRegistrationCleanupLoop


@pytest.mark.asyncio
async def test_registration_cleanup_tick_calls_service(monkeypatch) -> None:
    calls = 0

    async def cleanup_expired():
        nonlocal calls
        calls += 1
        return SimpleNamespace(expired_registrations=0, deleted_workers=0)

    monkeypatch.setattr(
        "antcode_master.ingester.worker_registration_cleanup_loop.registration_cleanup_service.cleanup_expired",
        cleanup_expired,
    )

    await WorkerRegistrationCleanupLoop()._tick()

    assert calls == 1
