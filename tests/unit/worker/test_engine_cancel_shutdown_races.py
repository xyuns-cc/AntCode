"""Regression tests for Worker cancellation and Engine shutdown races."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.domain.enums import RunStatus
from antcode_worker.domain.models import ExecPlan, ExecResult, RunContext
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.execution_admission import execute_with_admission
from antcode_worker.engine.state import RunState
from antcode_worker.executor.concurrency import ExecutionAdmission


class _DelayedAdmissionExecutor:
    def __init__(self) -> None:
        self.invoked = asyncio.Event()
        self.allow_registration = asyncio.Event()
        self.registered = False
        self.cancelled = False
        self.user_code_started = False

    def has_task(self, _run_id: str) -> bool:
        return self.registered

    async def cancel(self, _run_id: str) -> bool:
        if not self.registered:
            return False
        self.cancelled = True
        return True

    async def run(
        self,
        plan: ExecPlan,
        _runtime: object,
        log_sink: object = None,
        *,
        admission: ExecutionAdmission,
    ) -> ExecResult:
        del log_sink
        self.invoked.set()
        await self.allow_registration.wait()
        self.registered = True
        await admission.executor_ready()
        if not self.cancelled:
            self.user_code_started = True
        return ExecResult(run_id=plan.run_id, status=RunStatus.CANCELLED)


def _engine() -> Engine:
    transport = MagicMock()
    transport._worker_id = "worker-test"
    transport._lease_id = "lease-test"
    transport.report_result = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock(return_value=True)
    transport.release_run_ownership = AsyncMock(return_value=True)
    executor = MagicMock()
    executor.has_task.return_value = False
    executor.cancel = AsyncMock(return_value=False)
    executor.run = AsyncMock()
    return Engine(transport=transport, executor=executor, max_concurrent=1)


@pytest.mark.asyncio
async def test_cancel_after_dequeue_marks_request_without_premature_settlement() -> None:
    engine = _engine()
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="receipt-1")

    cancelled = await engine.cancel("run-1", reason="race")

    assert cancelled is True
    info = await engine.state_manager.get("run-1")
    assert info is not None
    assert info.state == RunState.QUEUED
    assert info.data["cancel_requested"] is True
    engine._transport.report_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_dequeued_worker_observes_cancel_before_starting_user_code() -> None:
    engine = _engine()
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="receipt-1")
    await engine.state_manager.request_cancel("run-1")
    context = RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="project-1",
    )

    result = await engine._execute_task(context, SimpleNamespace())

    assert result.status == RunStatus.CANCELLED
    engine._executor.run.assert_not_awaited()
    engine._transport.report_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_between_running_transition_and_executor_registration_blocks_user_code() -> None:
    engine = _engine()
    executor = _DelayedAdmissionExecutor()
    engine._executor = executor
    await engine.state_manager.add_if_new("run-1", "task-1")
    await engine.state_manager.transition("run-1", RunState.PREPARING)
    await engine.state_manager.transition("run-1", RunState.RUNNING)
    plan = ExecPlan(command="unused", run_id="run-1")
    execution = asyncio.create_task(
        execute_with_admission(
            engine,
            plan,
            runtime_handle=object(),
            log_sink=None,
        )
    )
    await executor.invoked.wait()

    assert await engine.cancel("run-1", reason="registration race") is True
    executor.allow_registration.set()
    result = await execution

    assert result.status == RunStatus.CANCELLED
    assert executor.cancelled is True
    assert executor.user_code_started is False


@pytest.mark.asyncio
async def test_cancel_preparing_task_awaits_preparation_cleanup() -> None:
    engine = _engine()
    await engine.state_manager.add_if_new("run-1", "task-1")
    engine._report_running_start = AsyncMock()  # type: ignore[method-assign]
    engine._prepare_runtime = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(path="/tmp", python_executable="python")
    )
    engine._build_payload = MagicMock(return_value=SimpleNamespace())  # type: ignore[method-assign]
    preparation_started = asyncio.Event()
    preparation_cleaned = asyncio.Event()

    async def build_plan(_context: object, _payload: object) -> ExecPlan:
        preparation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            preparation_cleaned.set()
            raise

    engine._plugin_registry = SimpleNamespace(build_plan=build_plan)
    context = RunContext(run_id="run-1", task_id="task-1", project_id="project-1")
    execution = asyncio.create_task(engine._execute_task(context, SimpleNamespace(source_bundle=None)))
    await preparation_started.wait()

    assert await engine.cancel("run-1", reason="stop preparation") is True
    result = await execution

    assert preparation_cleaned.is_set()
    assert result.status == RunStatus.CANCELLED
    info = await engine.state_manager.get("run-1")
    assert info is not None and info.state == RunState.CANCELLED
    engine._executor.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_stop_awaits_intake_active_and_draining_workers() -> None:
    engine = _engine()
    engine._running = True
    engine._polling = True
    engine._scheduler.stop = AsyncMock()
    completed: list[str] = []

    async def owned_task(name: str) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            completed.append(name)

    engine._poll_task = asyncio.create_task(owned_task("poll"))
    engine._control_task = asyncio.create_task(owned_task("control"))
    engine._ownership_renewal_task = asyncio.create_task(owned_task("renew"))
    shrink_task = asyncio.create_task(owned_task("shrink"))
    active_worker = asyncio.create_task(owned_task("active-worker"))
    draining_worker = asyncio.create_task(owned_task("draining-worker"))
    engine._worker_shrink_tasks = {shrink_task}
    engine._worker_tasks = [active_worker]
    engine._draining_worker_tasks = {draining_worker}
    await asyncio.sleep(0)

    await engine.stop(grace_period=0)

    assert set(completed) == {"poll", "control", "renew", "shrink", "active-worker", "draining-worker"}
    assert active_worker.done() and draining_worker.done()
    assert not engine._worker_tasks
    assert not engine._draining_worker_tasks
    assert engine._poll_task is None
    assert engine._control_task is None
    assert engine._ownership_renewal_task is None
    engine._scheduler.stop.assert_awaited_once()

    await engine.stop(grace_period=0)
    engine._scheduler.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_engine_stop_cleans_workers_after_intake_cleanup_failure() -> None:
    engine = _engine()
    engine._running = True
    engine._scheduler.stop = AsyncMock()
    worker_finished = asyncio.Event()

    async def failing_intake() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("intake cleanup failed") from exc

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            worker_finished.set()

    engine._poll_task = asyncio.create_task(failing_intake())
    engine._worker_tasks = [asyncio.create_task(worker())]
    await asyncio.sleep(0)

    with pytest.raises(ExceptionGroup, match="Engine 停止阶段失败"):
        await engine.stop(grace_period=0)

    assert worker_finished.is_set()
    engine._scheduler.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_engine_stop_keeps_ownership_renewal_alive_through_settlement() -> None:
    engine = _engine()
    engine._running = True
    engine._scheduler.stop = AsyncMock()
    await engine.state_manager.add_if_new("run-1", "task-1")
    renewal = asyncio.create_task(asyncio.Event().wait())
    engine._ownership_renewal_task = renewal

    async def settle_active_run() -> None:
        assert not renewal.done()
        await engine.state_manager.remove("run-1")

    engine._drain_tasks = settle_active_run  # type: ignore[method-assign]

    await engine.stop(grace_period=1)

    assert renewal.cancelled()
    engine._scheduler.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_terminate_uses_all_state_cancellation_path() -> None:
    engine = _engine()
    engine.cancel_all = AsyncMock(return_value=2)  # type: ignore[method-assign]

    await engine._force_terminate()

    engine.cancel_all.assert_awaited_once_with(reason="force_terminate")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_runtime_control_shutdown_timeout_is_exposed_after_cleanup() -> None:
    engine = _engine()
    pending = asyncio.create_task(asyncio.Event().wait())
    engine._inflight_controls.add(pending)

    with pytest.raises(TimeoutError, match="pending=1"):
        await engine._drain_runtime_controls(0)

    assert pending.cancelled()


@pytest.mark.asyncio
async def test_fenced_engine_stop_observes_failed_owned_task() -> None:
    engine = _engine()
    engine._ownership_fenced = True
    engine._scheduler.stop = AsyncMock()

    async def fail_renewal() -> None:
        raise RuntimeError("renewal failed")

    renewal = asyncio.create_task(fail_renewal())
    engine._ownership_renewal_task = renewal
    await asyncio.sleep(0)

    with pytest.raises(ExceptionGroup, match="Engine 停止阶段失败"):
        await engine.stop(grace_period=0)

    assert renewal.done()
    engine._scheduler.stop.assert_awaited_once()
