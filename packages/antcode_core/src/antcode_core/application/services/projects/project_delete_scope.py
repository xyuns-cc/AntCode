"""项目删除的作用域锁定与在途执行判定。

``TaskRun`` 分两族，两族都可能在途，"项目有没有活跃执行"必须同时覆盖：

- 计划任务 run：``task_id`` 指向真实 ``Task``，经 ``Task.project_id`` 归属项目；
- 爬取批次 run：``task_id`` 恒为 ``TASK_ID_ABSENT`` 哨兵，``scheduled_tasks`` 里永远
  没有对应行，经 ``result_data["crawl_batch_id"] → CrawlBatch.project_id`` 归属项目。

只按 ``Task`` 关联查在途 run，会让"批次正在爬"的项目通过无活跃执行检查被删掉：
``CrawlBatch`` 行随项目一起消失，在途 run 既查不到批次也查不到项目，变成孤儿。
批次身份提取复用 ``run_ownership.batch_id_of_run``，不另起一份判定。
"""

from __future__ import annotations

from dataclasses import dataclass

from antcode_core.application.services.run_ownership import batch_id_of_run
from antcode_core.domain.models.crawl import CrawlBatch
from antcode_core.domain.models.project import Project
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TASK_ID_ABSENT, TaskRun
from antcode_core.domain.models.task_status_sets import TASK_RUN_ACTIVE_STATUSES

ACTIVE_RUN_REJECTION = "项目存在未终态执行，请先取消并等待执行结束"


@dataclass(frozen=True)
class ProjectDeleteScope:
    """事务内已锁定的删除作用域（项目 + 其任务 + 其爬取批次）。"""

    project_id: int
    project_public_id: str
    task_ids: tuple[int, ...]
    batch_ids: tuple[str, ...]


async def lock_project_scope(conn, project_id) -> ProjectDeleteScope:
    """锁定项目/任务/批次并校验无在途执行，任一族在途即拒绝删除。

    P1-DB-04: 锁定与检查必须同在删除事务内。此前检查在事务外，与 scheduler
    ``_claim_task_run``（同样 select_for_update Task 行）之间存在窗口：检查通过
    后新 TaskRun 被创建，随后被本级联静默删除。先锁行后检查，并发创建要么先提交
    （下方检查看到并 abort），要么阻塞到删除提交后发现载体已消失而放弃。

    锁序固定 Project → Task → CrawlBatch，与并发写入方保持一致以避免死锁。
    """
    project = await Project.filter(id=project_id).using_db(conn).select_for_update().only("id", "public_id").first()
    if not project:
        raise ValueError("项目不存在")
    locked_tasks = (
        await Task.filter(project_id=project_id).using_db(conn).order_by("id").select_for_update().only("id").all()
    )
    task_ids = tuple(task.id for task in locked_tasks)
    batch_ids = await _lock_crawl_batch_ids(conn, project_id)
    if await _task_runs_active(conn, task_ids) or await _batch_runs_active(conn, batch_ids):
        raise ValueError(ACTIVE_RUN_REJECTION)
    return ProjectDeleteScope(int(project.id), str(project.public_id), task_ids, batch_ids)


async def _lock_crawl_batch_ids(conn, project_id) -> tuple[str, ...]:
    """在项目锁仍由当前事务持有时锁定并捕获全部 Crawl 批次。"""
    batch_ids = (
        await CrawlBatch.filter(project_id=project_id)
        .using_db(conn)
        .order_by("id")
        .select_for_update()
        .values_list("public_id", flat=True)
    )
    return tuple(str(batch_id) for batch_id in batch_ids)


async def _task_runs_active(conn, task_ids: tuple[int, ...]) -> bool:
    if not task_ids:
        return False
    return (
        await TaskRun.filter(task_id__in=list(task_ids), status__in=list(TASK_RUN_ACTIVE_STATUSES))
        .using_db(conn)
        .exists()
    )


async def _batch_runs_active(conn, batch_ids: tuple[str, ...]) -> bool:
    """批次 run 无法在 SQL 里直接按项目过滤。

    拦路的不是索引：``idx_task_executions_crawl_batch_id`` /
    ``idx_task_executions_crawl_batch_status`` 两条表达式索引就建在
    ``result_data->>'crawl_batch_id'`` 上（见 ``scripts/init_db_indexes.py``）。
    拦路的是 ORM——Tortoise 的 JSON 过滤在 sqlite 执行器上直接抛
    ``NotImplementedError``（``must be overridden in each executor``），而单测跑在
    sqlite 上。因此只取哨兵族的**在途**行 —— 正向 ``status__in`` 让
    ``(task_id, status)`` 复合索引可用，行数被集群在途并发度限住，不会退化成扫全部
    历史批次 run —— 再按批次归属判定。

    没有加 LIMIT/分页：结果集被在途并发度而非历史总量限住，ACTIVE 是终态取补、
    会随 run 结算自然排空。真机实测 ``task_executions`` 共 20 行、哨兵族 0 行，
    本查询当前返回空集。

    解析不出 ``crawl_batch_id`` 的行归属不到任何项目，不计入本项目的在途集合；
    与 ``resolve_run_owner_id`` 的"解析不出即无归属"契约一致。
    """
    if not batch_ids:
        return False
    runs = (
        await TaskRun.filter(task_id=TASK_ID_ABSENT, status__in=list(TASK_RUN_ACTIVE_STATUSES))
        .using_db(conn)
        .only("run_id", "result_data")
        .all()
    )
    wanted = set(batch_ids)
    return any(batch_id_of_run(run) in wanted for run in runs)


__all__ = ["ACTIVE_RUN_REJECTION", "ProjectDeleteScope", "lock_project_scope"]
