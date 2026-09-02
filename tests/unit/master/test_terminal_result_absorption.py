"""控制面先写终态后，Worker 的真实结果不许被无声吞掉。

控制面判的失败（驱逐 / 失租 / 超时）是**推测**：``run_settlement_guard`` 的模块
注释写得很直白——"PG 已被 eviction / no-ACK / 取消平面写成终态并不代表进程停了"。
Worker 后到的那份是**观测**。两者撞在同一个终态上时，旧实现走
``_is_stale_runtime_transition`` 返回 ``accepted=True, updates={}``：一行日志都没有，
result loop 照常 XACK，进程真的退了几号就此永久消失。

判决按**字段**分权，不是整条二选一：

* ``runtime_status`` / ``status`` / ``end_time`` / 重试 —— 归控制面。``settle_failure``
  在同一个事务里已经改了 Task 聚合计数并交付了 RetryIntent，单独回滚这一行不可能
  把那些下游事实一起回滚。
* ``exit_code`` —— 归 Worker。``failure_settlement._settlement_updates`` 里根本没有
  这个键，控制面永远产不出退出码，NULL 是"不知道"而不是"判过了"。没有冲突要仲裁。

被丢弃的那部分（Worker 自己的 error_message / output）留 WARNING，不进库。
"""

from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
import pytest_asyncio
from antcode_core.application.services.task_run_service import TaskRunService
from antcode_core.domain.models.enums import (
    DispatchStatus,
    RuntimeStatus,
    ScheduleType,
    TaskStatus,
    TaskType,
    WorkerStatus,
)
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.worker import Worker
from antcode_master.control.failure_settlement import (
    FailurePlane,
    FailureSettlementRequest,
    settle_failure,
)
from antcode_master.ingester.result_loop import ResultLoop
from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecResult, RunContext
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.gateway.codecs import TaskStatusEncoder
from loguru import logger
from tortoise import Tortoise

RUN_ID = "run-absorption"
WORKER_PUBLIC_ID = "worker-public-1"
LEASE_ID = "lease-1"
TOKEN = 11
PROCESS_EXIT_CODE = 7
OTHER_EXIT_CODE = 9
EVICTION_MESSAGE = "Worker 失联（lease expired）"
WORKER_MESSAGE = "boom"
DROP_ALARM = "结果晚于已写入的终态"
CONTEXT = RunContext(run_id=RUN_ID, task_id="task-public-1", project_id="project-1")


class _LeaseHolder:
    """``Engine._task_result`` 只需要的那个协作者。"""

    def _resolve_lease_id(self) -> str:
        return LEASE_ID


@pytest_asyncio.fixture
async def absorption_database(monkeypatch):
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "antcode_core.domain.models.scheduler_authority",
                "antcode_core.domain.models.task",
                "antcode_core.domain.models.task_run",
                "antcode_core.domain.models.worker",
            ]
        },
    )
    await Tortoise.generate_schemas()
    # 唯一被替换的协作者是 Redis 里的 lease 校验；结果提交与失败结算全是真实实现。
    # 必须显式取模块对象：``antcode_master.ingester`` 里有个同名的 loop **实例**，
    # 点号路径会 getattr 到实例上，patch 不到 ingest 真正读的模块全局。
    monkeypatch.setattr(
        import_module("antcode_master.ingester.result_loop"),
        "task_run_service",
        TaskRunService(_current_lease),
    )
    await SchedulerAuthority.create(name="master", fencing_token=TOKEN, activated_at=datetime.now(UTC))
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _current_lease(worker_id: str, lease_id: str) -> bool:
    return worker_id == WORKER_PUBLIC_ID and lease_id == LEASE_ID


async def _running_run() -> TaskRun:
    """一条已被 Worker 接单、正在跑的 run。"""
    worker = await Worker.create(
        name="worker-1",
        host="127.0.0.1",
        public_id=WORKER_PUBLIC_ID,
        status=WorkerStatus.ONLINE.value,
    )
    task = await Task.create(
        name="absorption",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=ScheduleType.ONCE,
        user_id=1,
        retry_count=0,
        status=TaskStatus.RUNNING,
    )
    return await TaskRun.create(
        task_id=task.id,
        run_id=RUN_ID,
        scheduler_fencing_token=TOKEN,
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=RuntimeStatus.RUNNING,
        status=TaskStatus.RUNNING,
        worker_id=worker.id,
        lease_id=LEASE_ID,
        start_time=datetime.now(UTC) - timedelta(seconds=5),
    )


async def _evict(run: TaskRun) -> None:
    """走 ``worker_eviction`` 用的同一个请求形状：RUNTIME 平面判 FAILED。"""
    settlement = await settle_failure(
        FailureSettlementRequest(
            run_id=run.run_id,
            authority_token=TOKEN,
            expected_scheduler_fencing_token=run.scheduler_fencing_token,
            plane=FailurePlane.RUNTIME,
            terminal_status=RuntimeStatus.FAILED,
            error_message=EVICTION_MESSAGE,
            status_at=datetime.now(UTC),
            expected_dispatch_statuses=frozenset({run.dispatch_status}),
            expected_runtime_statuses=frozenset({run.runtime_status}),
            expected_worker_id=run.worker_id,
            expected_lease_id=run.lease_id,
        )
    )
    assert settlement.settled is True


def _exec_result(status: RunStatus, exit_code: int | None) -> ExecResult:
    now = datetime.now(UTC)
    return ExecResult(
        run_id=RUN_ID,
        status=status,
        exit_code=exit_code,
        exit_reason=ExitReason.NORMAL if status is RunStatus.SUCCESS else ExitReason.ERROR,
        error_message=None if status is RunStatus.SUCCESS else WORKER_MESSAGE,
        started_at=now,
        finished_at=now,
    )


async def _report(status: RunStatus, exit_code: int | None):
    """Worker 的真实上报，走 Engine → proto → ResultLoop 生产链。"""
    task_result = Engine._task_result(_LeaseHolder(), CONTEXT, _exec_result(status, exit_code))
    return await ResultLoop()._commit_task_status(TaskStatusEncoder.encode(task_result, WORKER_PUBLIC_ID))


def _capture_logs() -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(message), level="WARNING")
    return records, sink_id


@pytest.mark.asyncio
async def test_worker_exit_code_survives_a_control_plane_settlement(absorption_database) -> None:
    """判据一（正）：控制面先判 FAILED，Worker 的 exit 7 仍要落库。

    改前这里是 ``None``——``failed/NULL`` 诚实但不完整，退出码永久丢失。
    """
    run = await _running_run()
    await _evict(run)

    outcome = await _report(RunStatus.FAILED, PROCESS_EXIT_CODE)

    persisted = await TaskRun.get(run_id=RUN_ID)
    assert outcome.accepted is True
    assert persisted.exit_code == PROCESS_EXIT_CODE


@pytest.mark.asyncio
async def test_the_dropped_worker_account_is_logged_not_swallowed(absorption_database) -> None:
    """判据二（正）：不落库的那部分必须留结构化记录。

    返回值判据在这条路径上会假绿——改前改后 ``accepted`` 都是 True。只认日志。
    """
    run = await _running_run()
    await _evict(run)

    records, sink_id = _capture_logs()
    try:
        await _report(RunStatus.FAILED, PROCESS_EXIT_CODE)
    finally:
        logger.remove(sink_id)

    alarm = "".join(records)
    assert DROP_ALARM in alarm
    assert WORKER_MESSAGE in alarm
    assert str(PROCESS_EXIT_CODE) in alarm


@pytest.mark.asyncio
async def test_the_control_plane_verdict_and_its_account_still_stand(absorption_database) -> None:
    """判据三（反）：只补退出码，判决与它的理由一个字节都不许动。

    ``settle_failure`` 那一笔事务同时改了 Task 聚合计数、交付了 RetryIntent，
    单独回滚这一行回滚不了那些下游事实。
    """
    run = await _running_run()
    await _evict(run)
    settled = await TaskRun.get(run_id=RUN_ID)

    await _report(RunStatus.FAILED, PROCESS_EXIT_CODE)

    persisted = await TaskRun.get(run_id=RUN_ID)
    assert persisted.error_message == EVICTION_MESSAGE
    assert persisted.runtime_status == RuntimeStatus.FAILED
    assert persisted.status == TaskStatus.FAILED
    assert persisted.end_time == settled.end_time
    assert persisted.duration_seconds == settled.duration_seconds


@pytest.mark.asyncio
async def test_a_first_report_still_persists_the_whole_result(absorption_database) -> None:
    """**控制组**：修复前后都绿。

    没有这条，把整条路径写死成"只写 exit_code"也能让上面几条变绿——那会让正常
    上报丢掉 error_message 与结束时间。
    """
    await _running_run()

    await _report(RunStatus.FAILED, PROCESS_EXIT_CODE)

    persisted = await TaskRun.get(run_id=RUN_ID)
    assert persisted.exit_code == PROCESS_EXIT_CODE
    assert persisted.error_message == WORKER_MESSAGE
    assert persisted.end_time is not None


@pytest.mark.asyncio
async def test_a_conflicting_terminal_is_still_rejected(absorption_database) -> None:
    """**控制组**：修复前后都绿。

    "同一终态补退出码"不许扩散成"任何后到的终态都吸收"。Worker 说 SUCCESS 而行上
    是 FAILED，是两个相反的判决，仍必须整条打回（进 DLQ），不能悄悄写个 exit 0。
    """
    run = await _running_run()
    await _evict(run)

    outcome = await _report(RunStatus.SUCCESS, 0)

    persisted = await TaskRun.get(run_id=RUN_ID)
    assert outcome.accepted is False
    assert persisted.runtime_status == RuntimeStatus.FAILED
    assert persisted.exit_code is None


@pytest.mark.asyncio
async def test_a_known_exit_code_is_never_replaced(absorption_database) -> None:
    """判据四（反）：补录只填空，不覆盖。

    Worker 正常报了 exit 7 之后，任何重投（含准备阶段失败那种无退出码的报文，
    以及另一个退出码）都不许改写已知的 7。
    """
    await _running_run()
    await _report(RunStatus.FAILED, PROCESS_EXIT_CODE)

    await _report(RunStatus.FAILED, None)
    await _report(RunStatus.FAILED, OTHER_EXIT_CODE)

    persisted = await TaskRun.get(run_id=RUN_ID)
    assert persisted.exit_code == PROCESS_EXIT_CODE
