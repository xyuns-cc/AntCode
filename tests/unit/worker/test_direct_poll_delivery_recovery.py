"""Direct task delivery recovery before scheduler handoff."""

from unittest.mock import AsyncMock, MagicMock

import antcode_worker.engine.poll_delivery_recovery as recovery_module
import pytest
from antcode_worker.domain.models import RunContext
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.ownership_fence import OwnershipFenceError, run_with_generation_fence
from antcode_worker.engine.state import RunState
from antcode_worker.transport.base import GenerationLostError


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["ownership", "enqueue"])
async def test_poll_failure_defers_exact_receipt(monkeypatch, failure_point):
    receipt = "{antcode}:task:ready:worker-1|1-0"
    task = MagicMock(
        task_id="task-1",
        project_id="project-1",
        run_id="run-1",
        timeout=30,
        priority=0,
        runtime_env_name="",
        receipt=receipt,
    )
    transport = MagicMock(is_connected=True, _worker_id="worker-1", _lease_id="lease-test")
    transport.poll_task = AsyncMock(return_value=task)
    transport.defer_task = AsyncMock(return_value=True)
    transport.claim_run_ownership = AsyncMock(return_value=True)
    transport.release_run_ownership = AsyncMock(return_value=True)
    engine = Engine(transport=transport, executor=MagicMock())
    engine._polling = True
    if failure_point == "ownership":
        transport.claim_run_ownership.side_effect = RuntimeError("claim failed")
    else:
        engine._scheduler.enqueue = AsyncMock(side_effect=RuntimeError("enqueue failed"))

    async def stop_after_failure(_delay):
        engine._polling = False

    monkeypatch.setattr(recovery_module.asyncio, "sleep", stop_after_failure)
    await engine._poll_loop()

    transport.defer_task.assert_awaited_once_with(
        receipt,
        reason="poll delivery failed before scheduler handoff",
    )
    assert await engine.state_manager.get("run-1") is None
    if failure_point == "enqueue":
        transport.release_run_ownership.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_settlement_does_not_retry_generation_loss():
    engine = Engine(transport=MagicMock(), executor=MagicMock())
    operation = AsyncMock(side_effect=GenerationLostError("superseded"))

    with pytest.raises(GenerationLostError, match="superseded"):
        await engine._settle_with_retry("settlement", operation)

    operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_loop_self_fences_on_generation_loss():
    transport = MagicMock()
    engine = Engine(transport=transport, executor=MagicMock())
    context = RunContext(run_id="run-1", task_id="task-1", project_id="project-1")
    task_message = MagicMock()
    engine._running = True
    engine._scheduler.dequeue = AsyncMock(return_value=("run-1", (context, task_message)))
    engine._scheduler.stop = AsyncMock()
    engine._execute_or_resume_settlement = AsyncMock(side_effect=GenerationLostError("superseded"))

    with pytest.raises(OwnershipFenceError, match="superseded"):
        await run_with_generation_fence(engine, lambda: engine._worker_loop(0))

    assert engine._running is False
    assert engine._polling is False
    assert isinstance(await engine.wait_for_fatal_error(), OwnershipFenceError)


@pytest.mark.asyncio
async def test_execute_task_does_not_convert_generation_loss_to_business_failure():
    engine = Engine(transport=MagicMock(), executor=MagicMock())
    context = RunContext(run_id="run-1", task_id="task-1", project_id="project-1")
    await engine.state_manager.add_if_new("run-1", task_id="task-1")
    await engine.state_manager.transition("run-1", RunState.QUEUED)
    engine._report_running_start = AsyncMock(side_effect=GenerationLostError("superseded"))

    with pytest.raises(GenerationLostError, match="superseded"):
        await engine._execute_task(context, MagicMock())

    info = await engine.state_manager.get("run-1")
    assert info is not None
    assert info.state is RunState.PREPARING
