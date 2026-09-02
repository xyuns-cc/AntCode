"""Worker run ownership 丢失后的 fail-closed/self-fence 回归测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.app.main import Application
from antcode_worker.domain.errors import CancellationError
from antcode_worker.domain.models import RunContext
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.ownership_fence import OwnershipFenceError
from antcode_worker.engine.state import RunState
from antcode_worker.executor.process import ProcessExecutor


async def _seed_running(engine: Engine, run_id: str = "run-1") -> None:
    await engine.state_manager.add_if_new(run_id, task_id="task-1")
    await engine.state_manager.transition(run_id, RunState.QUEUED)
    await engine.state_manager.transition(run_id, RunState.PREPARING)
    await engine.state_manager.transition(run_id, RunState.RUNNING)


def _engine(renew_result: object) -> Engine:
    transport = MagicMock()
    transport.renew_run_ownership = AsyncMock()
    if isinstance(renew_result, BaseException):
        transport.renew_run_ownership.side_effect = renew_result
    else:
        transport.renew_run_ownership.return_value = renew_result
    executor = MagicMock()
    executor.has_task = MagicMock(return_value=False)
    executor.cancel = AsyncMock(return_value=False)
    return Engine(transport=transport, executor=executor, max_concurrent=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("renew_result", [False, RuntimeError("redis unavailable")])
async def test_renew_failure_self_fences_entire_engine(renew_result: object) -> None:
    engine = _engine(renew_result)
    await _seed_running(engine)
    executing = asyncio.create_task(asyncio.Event().wait())
    engine._worker_tasks = [executing]
    engine._running = True
    engine._polling = True
    engine._ownership_renew_wakeup = asyncio.Event()
    engine._ownership_renew_wakeup.set()

    with pytest.raises(OwnershipFenceError):
        await engine._renew_run_ownership_loop()

    assert engine._running is False
    assert engine._polling is False
    assert executing.cancelled()
    assert isinstance(await engine.wait_for_fatal_error(), OwnershipFenceError)


@pytest.mark.asyncio
async def test_renew_loss_exposes_executor_kill_failure() -> None:
    engine = _engine(False)
    await _seed_running(engine)
    engine._executor.has_task.return_value = True
    engine._executor.cancel.return_value = False
    engine._running = True
    engine._polling = True
    engine._ownership_renew_wakeup = asyncio.Event()
    engine._ownership_renew_wakeup.set()

    with pytest.raises(OwnershipFenceError, match="未能终止"):
        await engine._renew_run_ownership_loop()

    fatal = await engine.wait_for_fatal_error()
    assert "未能终止" in str(fatal)
    assert engine._running is False
    assert engine._polling is False


@pytest.mark.asyncio
async def test_fenced_forced_cancel_never_reports_or_acks() -> None:
    engine = _engine(True)
    engine._ownership_fenced = True
    engine._report_result_by_info = AsyncMock()  # type: ignore[method-assign]
    context = RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="project-1",
        receipt="stream|1-0",
    )

    await engine._handle_forced_cancel(context, worker_id=0)

    engine._report_result_by_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelling_state_retries_executor_kill() -> None:
    engine = _engine(True)
    await _seed_running(engine)
    await engine.state_manager.transition("run-1", RunState.CANCELLING)
    engine._executor.has_task.return_value = True
    engine._executor.cancel.return_value = False

    with pytest.raises(CancellationError, match="未能终止"):
        await engine.cancel("run-1", reason="retry kill")

    engine._executor.cancel.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_process_executor_cancel_propagates_termination_error() -> None:
    executor = ProcessExecutor()
    await executor._register_task("run-1", object())
    executor._do_cancel = AsyncMock(side_effect=RuntimeError("SIGKILL failed"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="SIGKILL failed"):
        await executor.cancel("run-1")


@pytest.mark.asyncio
async def test_application_observes_engine_fatal_error() -> None:
    fatal = OwnershipFenceError("ownership lost")
    engine = SimpleNamespace(wait_for_fatal_error=AsyncMock(return_value=fatal))
    app = Application(SimpleNamespace(grace_period=0))
    app.container = SimpleNamespace(engine=engine)

    assert await app._wait_for_stop_reason() is fatal
