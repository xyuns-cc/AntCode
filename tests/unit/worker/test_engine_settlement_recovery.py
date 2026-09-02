from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.domain.enums import RunStatus
from antcode_worker.domain.models import ExecResult, RunContext
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.base import TaskMessage

REDELIVERY_ATTEMPTS = 2


def _engine() -> tuple[Engine, MagicMock]:
    transport = MagicMock()
    transport.report_result = AsyncMock(return_value=True)
    transport.ack_task = AsyncMock(return_value=True)
    transport._lease_id = "lease-1"
    engine = Engine(transport=transport, executor=MagicMock())
    engine._SETTLE_MAX_ATTEMPTS = 1
    engine._SETTLE_BACKOFF_BASE_SECONDS = 0
    engine._release_run_ownership = AsyncMock()
    return engine, transport


def _context(receipt: str) -> RunContext:
    return RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="project-1",
        receipt=receipt,
    )


def _message(receipt: str) -> TaskMessage:
    return TaskMessage(
        task_id="task-1",
        project_id="project-1",
        run_id="run-1",
        receipt=receipt,
    )


@pytest.mark.asyncio
async def test_report_failure_redelivery_resumes_result_without_execution() -> None:
    engine, transport = _engine()
    transport.report_result = AsyncMock(side_effect=[False, True])
    result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="ready|1-0")

    with pytest.raises(RuntimeError, match="结果上报失败"):
        await engine._report_result(_context("ready|1-0"), result)

    assert await engine._admit_polled_task("run-1", _message("ready|1-0")) is True
    engine._execute_task = AsyncMock()
    recovered = await engine._execute_or_resume_settlement(_context("ready|1-0"), _message("ready|1-0"))
    await engine._report_result(_context("ready|1-0"), recovered)

    engine._execute_task.assert_not_awaited()
    assert transport.report_result.await_count == REDELIVERY_ATTEMPTS
    assert await engine.state_manager.get("run-1") is None
    engine._release_run_ownership.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_ack_failure_redelivery_skips_already_reported_result() -> None:
    engine, transport = _engine()
    transport.ack_task = AsyncMock(side_effect=[False, True])
    result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="ready|1-0")

    with pytest.raises(RuntimeError, match="任务 ACK 失败"):
        await engine._report_result(_context("ready|1-0"), result)

    assert await engine._admit_polled_task("run-1", _message("ready|1-0")) is True
    await engine._report_result(_context("ready|1-0"), result)

    transport.report_result.assert_awaited_once()
    assert transport.ack_task.await_count == REDELIVERY_ATTEMPTS
    assert await engine.state_manager.get("run-1") is None


@pytest.mark.asyncio
async def test_distinct_receipt_arriving_during_failure_is_acked_without_reexecution() -> None:
    engine, transport = _engine()
    transport.ack_task = AsyncMock(side_effect=[False, True, True])
    result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="ready|1-0")

    with pytest.raises(RuntimeError, match="任务 ACK 失败"):
        await engine._report_result(_context("ready|1-0"), result)

    assert await engine._admit_polled_task("run-1", _message("ready|2-0")) is True
    engine._execute_task = AsyncMock()
    recovered = await engine._execute_or_resume_settlement(_context("ready|2-0"), _message("ready|2-0"))
    await engine._report_result(_context("ready|2-0"), recovered)

    engine._execute_task.assert_not_awaited()
    acked = {call.args[0] for call in transport.ack_task.await_args_list[1:]}
    assert acked == {"ready|1-0", "ready|2-0"}
    transport.report_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_settlement_is_counted_and_renewed_as_active() -> None:
    engine, _ = _engine()
    result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="ready|1-0")
    await engine.state_manager.start_settlement(
        "run-1",
        task_id="task-1",
        receipt="ready|1-0",
        result=result,
    )
    await engine.state_manager.release_settlement("run-1")
    engine._renew_one_run_ownership = AsyncMock()

    assert await engine.state_manager.count_active() == 1
    await engine._renew_active_run_ownership()

    engine._renew_one_run_ownership.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["report", "ack"])
async def test_transport_exception_keeps_settlement_recoverable(failure_stage: str) -> None:
    engine, transport = _engine()
    expected_error = "result unavailable"
    if failure_stage == "report":
        transport.report_result = AsyncMock(side_effect=RuntimeError(expected_error))
    else:
        expected_error = "ack unavailable"
        transport.ack_task = AsyncMock(side_effect=RuntimeError(expected_error))
    result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="ready|1-0")

    with pytest.raises(RuntimeError, match=expected_error):
        await engine._report_result(_context("ready|1-0"), result)

    info = await engine.state_manager.get("run-1")
    assert info is not None
    assert info.settlement_result == result
    assert info.settlement_active is False
    engine._release_run_ownership.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_arriving_during_settlement_joins_same_ack_cycle() -> None:
    engine, transport = _engine()
    ack_started = asyncio.Event()
    release_ack = asyncio.Event()

    async def ack(receipt: str, *, accepted: bool) -> bool:
        if receipt == "ready|1-0":
            ack_started.set()
            await release_ack.wait()
        return accepted

    transport.ack_task = AsyncMock(side_effect=ack)
    result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
    await engine.state_manager.add_if_new("run-1", "task-1", receipt="ready|1-0")
    settlement = asyncio.create_task(engine._report_result(_context("ready|1-0"), result))
    await ack_started.wait()

    assert await engine._admit_polled_task("run-1", _message("ready|2-0")) is False
    release_ack.set()
    await settlement

    acked = {call.args[0] for call in transport.ack_task.await_args_list}
    assert acked == {"ready|1-0", "ready|2-0"}
    assert await engine.state_manager.get("run-1") is None
