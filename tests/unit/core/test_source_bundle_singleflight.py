import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from antcode_core.application.services.projects import source_bundle_service as module
from antcode_core.application.services.projects.source_bundle_singleflight import AsyncSingleFlight

from tests.unit.core.test_source_bundle_service import FakeStore

EXPECTED_OPERATION_CALLS = 2


@pytest.mark.asyncio
async def test_concurrent_bundle_requests_share_one_build(monkeypatch) -> None:
    store = FakeStore()
    started = asyncio.Event()
    release = asyncio.Event()
    materialize = Mock(return_value=b"bundle")

    async def fake_auth(_url, _credential_id):
        started.set()
        await release.wait()

    monkeypatch.setattr(module.git_credential_service, "build_auth_config", fake_auth)
    monkeypatch.setattr(module, "_resolve_git_revision", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setattr(module, "_materialize_bundle", materialize)
    service = module.SourceBundleService(store)
    source = {"url": "https://example.com/repo.git"}

    first = asyncio.create_task(
        service.create_git_source_bundle(project_public_id="p", source_config=source, entry_point="main.py")
    )
    await started.wait()
    second = asyncio.create_task(
        service.create_git_source_bundle(project_public_id="p", source_config=source, entry_point="main.py")
    )
    await asyncio.sleep(0)
    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is second_result
    assert materialize.call_count == 1


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_cancel_shared_operation() -> None:
    singleflight = AsyncSingleFlight[str, str]()
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation() -> str:
        started.set()
        await release.wait()
        return "done"

    cancelled_waiter = asyncio.create_task(singleflight.run("key", operation))
    await started.wait()
    surviving_waiter = asyncio.create_task(singleflight.run("key", operation))
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    release.set()

    assert await surviving_waiter == "done"


@pytest.mark.asyncio
async def test_failure_propagates_and_next_call_retries() -> None:
    singleflight = AsyncSingleFlight[str, str]()
    operation = AsyncMock(side_effect=[RuntimeError("build failed"), "recovered"])

    with pytest.raises(RuntimeError, match="build failed"):
        await singleflight.run("key", operation)

    assert await singleflight.run("key", operation) == "recovered"
    assert operation.await_count == EXPECTED_OPERATION_CALLS


@pytest.mark.asyncio
async def test_cancelled_only_waiter_does_not_leave_unretrieved_failure() -> None:
    singleflight = AsyncSingleFlight[str, str]()
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation() -> str:
        started.set()
        await release.wait()
        raise RuntimeError("detached build failed")

    waiter = asyncio.create_task(singleflight.run("key", operation))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
