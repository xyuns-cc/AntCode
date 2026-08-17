"""一次性任务的两条派发路径必须共用同一个去重域。

耐久扫描（``DurableScheduleRunner``）与手动触发（``trigger_idempotency``）
过去各自取 run 身份：前者 ``uuid5(SCHEDULED_NS, task:fire_time)``，后者
``uuid5(TRIGGER_NS, task:outbox_id)``。两个域不相交 ⇒ 同一个 ONCE/DATE 任务
被各建一个 run 执行两次。下面的用例把"同一逻辑执行 = 同一 run 身份"钉死：
既有结构性断言（身份函数本身），也有走真实代码路径的行为断言（run 计数）。
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from antcode_core.application.services.scheduler.trigger_identity import (
    dispatch_run_id,
    scheduled_run_id,
    trigger_run_id,
)
from antcode_core.domain.models.enums import DispatchStatus, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.models.scheduler_authority import SchedulerAuthority
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun
from antcode_master.control import scheduler_loop
from antcode_master.control.durable_schedule import one_time_schedule_is_fulfilled
from antcode_master.control.execution_parameters import RecoveryExecutionOptions
from antcode_master.control.trigger_idempotency import target_run_id
from tortoise import Tortoise

OUTBOX_ID = "outbox-1"
TOKEN = 5


@pytest_asyncio.fixture
async def dispatch_database():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "antcode_core.domain.models.scheduler_authority",
                "antcode_core.domain.models.task",
                "antcode_core.domain.models.task_run",
            ]
        },
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _task(schedule_type=ScheduleType.ONCE, scheduled_time=None) -> Task:
    return await Task.create(
        name=f"task-{datetime.now(UTC).timestamp()}",
        project_id=1,
        task_type=TaskType.CODE,
        schedule_type=schedule_type,
        scheduled_time=scheduled_time,
        is_active=True,
        user_id=1,
    )


def _service(created: list[str]):
    """带真实 ``trigger_task`` 的调度服务；``_execute_task`` 换成建 run 的桩。

    桩故意不带任何并发上限判断：双跑防护必须来自 run 身份本身，而不是
    ``max_instances`` 恰好为 1 这种旁路。
    """
    service = scheduler_loop.SchedulerService()

    async def execute(task_id, *, fixed_run_id=None, **_kwargs):
        await TaskRun.create(
            task_id=task_id,
            run_id=fixed_run_id,
            status=TaskStatus.RUNNING,
            runtime_status=TaskStatus.RUNNING,
            dispatch_status=DispatchStatus.DISPATCHED,
            scheduler_fencing_token=TOKEN,
        )
        created.append(fixed_run_id)
        return fixed_run_id

    service._execute_task = execute
    return service


def test_one_time_dispatch_paths_resolve_to_the_same_run_identity() -> None:
    task = Task(id=7, schedule_type=ScheduleType.ONCE, scheduled_time=None, created_at=datetime.now(UTC))

    assert target_run_id(task, OUTBOX_ID, None) == scheduled_run_id(task)
    assert dispatch_run_id(task, OUTBOX_ID) == scheduled_run_id(task)
    assert target_run_id(task, OUTBOX_ID, None) != trigger_run_id(task.id, OUTBOX_ID)


def test_dated_one_time_task_shares_the_same_identity_as_well() -> None:
    fire_at = datetime.now(UTC) - timedelta(hours=2)
    task = Task(id=8, schedule_type=ScheduleType.DATE, scheduled_time=fire_at, created_at=datetime.now(UTC))

    assert target_run_id(task, OUTBOX_ID, None) == scheduled_run_id(task)


def test_recurring_task_trigger_keeps_its_own_outbox_identity() -> None:
    task = Task(id=9, schedule_type=ScheduleType.CRON, scheduled_time=None, created_at=datetime.now(UTC))

    assert target_run_id(task, OUTBOX_ID, None) == trigger_run_id(task.id, OUTBOX_ID)


def test_recovery_trigger_keeps_a_successor_identity() -> None:
    """恢复触发建的是被中断 run 的后继，折叠回调度槽位会让恢复变成空操作。"""
    task = Task(id=10, schedule_type=ScheduleType.ONCE, scheduled_time=None, created_at=datetime.now(UTC))
    options = RecoveryExecutionOptions("run-interrupted", {})

    run_id = target_run_id(task, "recovery:run-interrupted", options)

    assert run_id == trigger_run_id(task.id, "recovery:run-interrupted")
    assert run_id != scheduled_run_id(task)


@pytest.mark.asyncio
async def test_durable_scan_after_manual_trigger_creates_no_second_run(dispatch_database) -> None:
    created: list[str] = []
    task = await _task()
    service = _service(created)

    triggered = await service.trigger_task(task.id, idempotency_key=OUTBOX_ID)
    await service._durable_schedule.execute_task(await Task.get(id=task.id), TOKEN)

    assert triggered == scheduled_run_id(task)
    assert created == [scheduled_run_id(task)]
    assert await TaskRun.filter(task_id=task.id).count() == 1


@pytest.mark.asyncio
async def test_manual_trigger_after_durable_scan_creates_no_second_run(dispatch_database) -> None:
    created: list[str] = []
    task = await _task()
    service = _service(created)

    await service._durable_schedule.execute_task(task, TOKEN)
    triggered = await service.trigger_task(task.id, idempotency_key=OUTBOX_ID)

    assert triggered == scheduled_run_id(task)
    assert created == [scheduled_run_id(task)]
    assert await TaskRun.filter(task_id=task.id).count() == 1


@pytest.mark.asyncio
async def test_dated_task_dispatched_late_is_not_run_twice_by_a_manual_trigger(dispatch_database) -> None:
    """根因与 ONCE 的"创建即到期"无关：过期 DATE 任务同样命中同一条路径。"""
    created: list[str] = []
    task = await _task(ScheduleType.DATE, datetime.now(UTC) - timedelta(hours=2))
    service = _service(created)

    await service.trigger_task(task.id, idempotency_key=OUTBOX_ID)
    await service._durable_schedule.execute_task(await Task.get(id=task.id), TOKEN)

    assert await TaskRun.filter(task_id=task.id).count() == 1


@pytest.mark.asyncio
async def test_schedule_slot_stays_open_while_a_retry_intent_is_pending(dispatch_database) -> None:
    task = await _task()
    await SchedulerAuthority.create(name="master", fencing_token=TOKEN, activated_at=datetime.now(UTC))
    run = await TaskRun.create(
        task_id=task.id,
        run_id=scheduled_run_id(task),
        status=TaskStatus.FAILED,
        runtime_status=TaskStatus.FAILED,
        dispatch_status=DispatchStatus.DISPATCHED,
        next_retry_at=datetime.now(UTC) + timedelta(seconds=1),
        scheduler_fencing_token=TOKEN,
    )
    service = scheduler_loop.SchedulerService()

    assert await one_time_schedule_is_fulfilled(task.id) is False
    await service._durable_schedule.execute_task(task, TOKEN)
    assert (await Task.get(id=task.id)).is_active is True

    await TaskRun.filter(id=run.id).update(next_retry_at=None)
    assert await one_time_schedule_is_fulfilled(task.id) is True
    await service._durable_schedule.execute_task(await Task.get(id=task.id), TOKEN)
    assert (await Task.get(id=task.id)).is_active is False
