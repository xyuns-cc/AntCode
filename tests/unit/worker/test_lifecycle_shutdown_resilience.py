"""Lifecycle shutdown must clean every component and expose all failures."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_worker.app.lifecycle import Lifecycle
from antcode_worker.app.main import Application


def _component(*, failure: Exception | None = None):
    component = SimpleNamespace(stop=AsyncMock())
    if failure is not None:
        component.stop.side_effect = failure
    return component


@pytest.mark.asyncio
async def test_shutdown_runs_all_steps_and_raises_aggregate() -> None:
    engine = SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError("engine-stop")))
    transport = SimpleNamespace(deregister=AsyncMock(), stop=AsyncMock())
    heartbeat = _component(failure=ValueError("heartbeat-stop"))
    executor = _component()
    runtime = _component()
    observability = _component()
    container = SimpleNamespace(
        engine=engine,
        transport=transport,
        heartbeat_reporter=heartbeat,
        executor=executor,
        runtime_manager=runtime,
        observability_server=observability,
    )
    lifecycle = Lifecycle()
    lifecycle._running = True
    lifecycle._shutdown_event = asyncio.Event()

    with pytest.raises(ExceptionGroup) as raised:
        await lifecycle.shutdown(container, grace_period=2)

    messages = {str(error) for error in raised.value.exceptions}
    assert messages == {"engine-stop", "heartbeat-stop"}
    transport.deregister.assert_awaited_once_with("worker_shutdown")
    transport.stop.assert_awaited_once_with(grace_period=5.0)
    executor.stop.assert_awaited_once()
    runtime.stop.assert_awaited_once()
    observability.stop.assert_awaited_once()
    assert lifecycle._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_application_shutdown_exposes_timeout_and_still_closes_database(monkeypatch) -> None:
    async def never_finishes(*_args) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("antcode_worker.app.main.SHUTDOWN_TIMEOUT_MARGIN_SECONDS", 0.0)
    app = Application(SimpleNamespace(grace_period=0.0))
    app.container = SimpleNamespace()
    app.lifecycle.shutdown = AsyncMock(side_effect=never_finishes)
    app._close_database = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(TimeoutError):
        await app._shutdown_with_timeout()

    app._close_database.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_application_shutdown_aggregates_component_and_database_failures() -> None:
    app = Application(SimpleNamespace(grace_period=1.0))
    app.container = SimpleNamespace()
    app.lifecycle.shutdown = AsyncMock(side_effect=RuntimeError("component cleanup"))
    app._close_database = AsyncMock(side_effect=ValueError("database cleanup"))  # type: ignore[method-assign]

    with pytest.raises(ExceptionGroup) as raised:
        await app._shutdown_with_timeout()

    assert {str(error) for error in raised.value.exceptions} == {
        "component cleanup",
        "database cleanup",
    }
