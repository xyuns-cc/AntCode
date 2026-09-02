"""退出码必须区分"任务自己退了几号"与"控制面判它失败"。

两者都会落 ``status=failed``；只有 ``exit_code`` 能把它们分开。任何把
"没有退出码"折叠成 ``0`` 的环节，都会让按 ``exit_code == 0`` 判成功的自动化
把一次真实失败读成成功，也让前端把失败 run 的退出码标成绿色。

用例逐条走**生产链**：``Engine`` 造 ``TaskResult`` → ``TaskStatusEncoder``
编 proto → ``ResultLoop._commit_task_status`` 落库，不复刻任何映射。
"""

from datetime import UTC, datetime
from importlib import import_module

import pytest
import pytest_asyncio
from antcode_core.application.services.task_run_service import TaskRunService
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus, WorkerStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.models.worker import Worker
from antcode_master.ingester.result_loop import ResultLoop
from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecResult, RunContext
from antcode_worker.engine.engine import Engine
from antcode_worker.transport.gateway.codecs import TaskStatusEncoder
from tortoise import Tortoise

RUN_ID = "run-exit-code"
WORKER_PUBLIC_ID = "worker-public-1"
LEASE_ID = "lease-1"
PROCESS_EXIT_CODE = 7
CONTEXT = RunContext(run_id=RUN_ID, task_id="task-public-1", project_id="project-1")


class _LeaseHolder:
    """``Engine`` 上报路径只需要的两个协作者。"""

    def __init__(self) -> None:
        self.reported: list[object] = []

    def _resolve_lease_id(self) -> str:
        return LEASE_ID

    @property
    def _transport(self) -> "_LeaseHolder":
        return self

    async def report_result(self, result: object) -> bool:
        self.reported.append(result)
        return True


@pytest_asyncio.fixture
async def result_database(monkeypatch):
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "antcode_core.domain.models.task",
                "antcode_core.domain.models.task_run",
                "antcode_core.domain.models.worker",
            ]
        },
    )
    await Tortoise.generate_schemas()
    # 唯一被替换的协作者是 Redis 里的 lease 校验；结果提交本体全是真实实现。
    # 必须显式拿模块对象：``antcode_master.ingester`` 里有个同名的 loop **实例**，
    # 点号路径（含 monkeypatch 的字符串形式）会 getattr 到实例上，patch 不到
    # ingest 真正读的模块全局，测试于是去连真 Redis 并挂死。
    monkeypatch.setattr(
        import_module("antcode_master.ingester.result_loop"),
        "task_run_service",
        TaskRunService(_current_lease),
    )
    try:
        worker = await Worker.create(
            name="worker-1",
            host="127.0.0.1",
            public_id=WORKER_PUBLIC_ID,
            status=WorkerStatus.ONLINE.value,
        )
        await TaskRun.create(
            task_id=1,
            run_id=RUN_ID,
            status=TaskStatus.QUEUED,
            dispatch_status=DispatchStatus.DISPATCHED,
            worker_id=worker.id,
            lease_id=LEASE_ID,
        )
        yield
    finally:
        await Tortoise.close_connections()


async def _current_lease(worker_id: str, lease_id: str) -> bool:
    return worker_id == WORKER_PUBLIC_ID and lease_id == LEASE_ID


async def _commit(task_result: object) -> None:
    status = TaskStatusEncoder.encode(task_result, WORKER_PUBLIC_ID)
    outcome = await ResultLoop()._commit_task_status(status)
    assert outcome.accepted


async def _report_running() -> None:
    holder = _LeaseHolder()
    await Engine._report_running_start(holder, CONTEXT, datetime.now(UTC))
    await _commit(holder.reported[0])


async def _report_terminal(result: ExecResult) -> None:
    await _commit(Engine._task_result(_LeaseHolder(), CONTEXT, result))


def _exec_result(status: RunStatus, exit_code: int | None) -> ExecResult:
    now = datetime.now(UTC)
    return ExecResult(
        run_id=RUN_ID,
        status=status,
        exit_code=exit_code,
        exit_reason=ExitReason.NORMAL if status is RunStatus.SUCCESS else ExitReason.ERROR,
        error_message=None if status is RunStatus.SUCCESS else "boom",
        started_at=now,
        finished_at=now,
    )


async def _persisted_exit_code() -> int | None:
    run = await TaskRun.get(run_id=RUN_ID)
    return run.exit_code


@pytest.mark.asyncio
async def test_running_report_records_no_exit_code(result_database):
    """RUNNING 上报不得预先落一个 0——它是后续所有伪造 0 的来源。"""
    await _report_running()

    assert await _persisted_exit_code() is None


@pytest.mark.asyncio
async def test_process_exit_code_is_persisted_verbatim(result_database):
    await _report_running()
    await _report_terminal(_exec_result(RunStatus.FAILED, PROCESS_EXIT_CODE))

    assert await _persisted_exit_code() == PROCESS_EXIT_CODE


@pytest.mark.asyncio
async def test_control_plane_failure_has_no_exit_code(result_database):
    """准备阶段失败（拉包/运行时/插件）没有进程退出码，不能谎报 0。"""
    await _report_running()
    await _report_terminal(_exec_result(RunStatus.FAILED, None))

    run = await TaskRun.get(run_id=RUN_ID)
    assert run.status == TaskStatus.FAILED
    assert run.exit_code is None


@pytest.mark.asyncio
async def test_real_zero_exit_code_survives(result_database):
    """反向判据：真的退出 0 必须仍然存成 0，而不是被一并抹成 NULL。"""
    await _report_running()
    await _report_terminal(_exec_result(RunStatus.SUCCESS, 0))

    assert await _persisted_exit_code() == 0
