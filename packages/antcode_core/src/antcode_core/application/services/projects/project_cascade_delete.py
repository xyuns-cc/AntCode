"""项目级联删除（单事务原子 + 提交后日志清扫）。

P1-20 修复要点:
- 全流程包裹在 ``in_transaction()`` 里,任一步骤失败整体回滚,不会
  出现"任务清了、项目还在"的半状态。
- 补漏此前遗漏的子表: ProjectSource(一对一)、RunSourceSnapshot(一对多)。
- GitCredential **不**跟随项目删除:它 owner_user_id 归属用户,可能被同一
  用户的多个项目复用,由 user_service.delete_user 负责清理。

P1-FN-07: 批量删 Task 时为每个被删 task 发布 task_changed outbox 事件
（与删除同事务提交），Master scheduler_event_loop 消费后发现 Task 已不
存在即移除对应 APScheduler job。

P1-DB-03: 事务提交后按 run 级 advisory lock 串行化清扫日志残留
（``task_log_run_guard.purge_task_logs_for_runs``）。
"""

from typing import cast

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.domain.models.project import Project, ProjectCode, ProjectFile, ProjectRule
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun


async def delete_project_cascade(project_id) -> dict:
    """级联删除项目及其相关数据，返回各表删除计数。"""
    from antcode_core.application.services.logs.task_log_run_guard import (
        purge_task_logs_for_runs,
    )

    deleted = {
        "tasks": 0,
        "executions": 0,
        "task_logs": 0,
        "details": 0,
        "runtime_bindings": 0,
        "project_sources": 0,
        "run_source_snapshots": 0,
        "crawl_batches": 0,
    }

    try:
        async with in_transaction() as conn:
            task_ids, cleanup_run_ids = await _delete_tasks_and_runs(conn, project_id, deleted)
            await _publish_task_changed_events(conn, task_ids)
            await _delete_project_relations(project_id, deleted)
            # P1-round6 5.2 durable cleanup: 事务内 enqueue task_logs_purge
            # outbox 事件, 崩溃后由 outbox publisher 自动重投, 消费者
            # scheduler_event_loop._purge_task_logs 走同 advisory lock 幂等
            # 消费。之前只靠事务后同步调用, 崩溃即孤儿。
            await _publish_task_logs_purge_events(conn, project_id, cleanup_run_ids)

        # P1-DB-03: 事务提交后按 run 级 advisory lock 串行化清扫日志残留
        # (语义见 task_log_run_guard 模块注释)。事务内的 TaskLog 批删不持
        # run 锁,与在途 append_entries 存在竞态窗口;此处 TaskRun 已删除,
        # 持同一把锁清扫窗口内提交的残留,之后任何新 append 都会在锁内
        # EXISTS 校验中被明确拒绝。
        # P1-round6 5.2: 改为 best-effort — outbox 事件已入库, 此处失败
        # 也能由 outbox 消费兜底; 不再上抛防止 delete API 假失败误导用户。
        try:
            deleted["task_logs"] += await purge_task_logs_for_runs(cleanup_run_ids)
        except Exception as exc:  # noqa: BLE001 - outbox 已兜底
            logger.warning(
                "级联删除同步 purge_task_logs 失败, 等待 outbox 消费兜底: project={} err={}",
                project_id,
                exc,
            )

        logger.info(f"级联删除项目 {project_id}: {deleted}")
        return deleted
    except Exception as e:
        logger.error(f"级联删除项目 {project_id} 失败: {e}")
        raise


async def _lock_project_and_tasks(conn, project_id) -> tuple[str, list[int]]:
    """P1-DB-04: 项目/任务锁定与活动 run 检查必须在事务内完成。

    此前检查在事务外，与 scheduler ``_claim_task_run``（同样
    select_for_update Task 行）之间存在窗口：检查通过后新 TaskRun 被创建，
    随后被本级联静默删除。现在先锁 Task 行，并发创建要么先提交（下方检查
    看到并 abort），要么阻塞到删除提交后发现 Task 已消失而放弃。
    """
    from antcode_core.application.services.crawl.spider_storage_cleanup import (
        SPIDER_WRITABLE_TASK_STATUSES,
    )

    project = await Project.filter(id=project_id).using_db(conn).select_for_update().only("id", "public_id").first()
    if not project:
        raise ValueError("项目不存在")
    locked_tasks = await Task.filter(project_id=project_id).using_db(conn).select_for_update().only("id").all()
    task_ids = [task.id for task in locked_tasks]
    if (
        task_ids
        and await TaskRun.filter(
            task_id__in=list(task_ids),
            status__in=SPIDER_WRITABLE_TASK_STATUSES,
        )
        .using_db(conn)
        .exists()
    ):
        raise ValueError("项目存在未终态执行，请先取消并等待执行结束")
    return project.public_id, task_ids


async def _delete_tasks_and_runs(conn, project_id, deleted: dict) -> tuple[list[int], list[str]]:
    """锁定校验后删除任务/执行记录/事务内日志，返回 (task_ids, run_ids)。"""
    from antcode_core.application.services.crawl.spider_storage_cleanup import (
        iter_cleanup_run_batches,
    )
    from antcode_core.application.services.scheduler.outbox_service import (
        scheduler_outbox_service,
    )
    from antcode_core.domain.models import TaskLog

    project_public_id, task_ids = await _lock_project_and_tasks(conn, project_id)

    # 1. 任务 → 执行记录(先删 child 避免悬挂)
    cleanup_run_ids: list[str] = []
    if task_ids:
        run_ids = await TaskRun.filter(task_id__in=list(task_ids)).values_list("run_id", flat=True)
        cleanup_run_ids = [str(run_id) for run_id in run_ids if run_id]
        for run_batch in iter_cleanup_run_batches(cast(list[str], run_ids)):
            deleted["task_logs"] += await TaskLog.filter(run_id__in=list(run_batch)).delete()
            await scheduler_outbox_service.enqueue(
                event_type="spider_storage_cleanup",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={"run_ids": list(run_batch), "project_id": str(project_public_id)},
                connection=conn,
            )
        deleted["executions"] = await TaskRun.filter(task_id__in=list(task_ids)).delete()

    # 2. 任务本体
    deleted["tasks"] = await Task.filter(project_id=project_id).delete()
    return task_ids, cleanup_run_ids


async def _publish_task_logs_purge_events(conn, project_id, run_ids: list[str]) -> None:
    """P1-round6 5.2 durable cleanup: 分批 enqueue task_logs_purge 事件。

    每批 ≤ 200 run_id, 避免单事件 payload 过大。事件与删除同事务提交,
    保证 "TaskRun 已删且 log purge 事件已入库" 的原子性;崩溃后 outbox
    publisher 重投, scheduler_event_loop._purge_task_logs 消费兜底。
    """
    from antcode_core.application.services.scheduler.outbox_service import (
        scheduler_outbox_service,
    )

    if not run_ids:
        return
    batch_size = 200
    for i in range(0, len(run_ids), batch_size):
        batch = run_ids[i : i + batch_size]
        await scheduler_outbox_service.enqueue(
            event_type="task_logs_purge",
            aggregate_type="project",
            aggregate_id=project_id,
            payload={"run_ids": batch, "project_id": str(project_id)},
            connection=conn,
        )


async def _publish_task_changed_events(conn, task_ids: list[int]) -> None:
    """P1-FN-07: 与单任务删除路径(scheduler_service.delete_task)一致,为每个
    被删 task 发布 task_changed outbox 事件(与删除同事务提交)。Master
    scheduler_event_loop 消费后发现 Task 已不存在即移除对应 APScheduler
    job;否则周期 job 会持续报错直到 Master 重启。"""
    from antcode_core.application.services.scheduler.outbox_service import (
        scheduler_outbox_service,
    )

    for task_internal_id in task_ids:
        await scheduler_outbox_service.enqueue(
            event_type="task_changed",
            aggregate_type="task",
            aggregate_id=task_internal_id,
            payload={"task_id": str(task_internal_id)},
            connection=conn,
        )


async def _delete_project_relations(project_id, deleted: dict) -> None:
    """删除项目详情/绑定/来源/快照/批次与项目本体（事务内顺序执行）。"""
    from antcode_core.domain.models import (
        ProjectRuntimeBinding,
        ProjectSource,
        RunSourceSnapshot,
    )
    from antcode_core.domain.models.crawl import CrawlBatch

    # 3. 项目详情(file / rule / code 至多命中其一)；顺序执行避免共享连接并发冲突
    d_file = await ProjectFile.filter(project_id=project_id).delete()
    d_rule = await ProjectRule.filter(project_id=project_id).delete()
    d_code = await ProjectCode.filter(project_id=project_id).delete()
    deleted["details"] = d_file + d_rule + d_code

    # 4. 运行时绑定
    deleted["runtime_bindings"] = await ProjectRuntimeBinding.filter(project_id=project_id).delete()

    # 5. ProjectSource(Git 源配置, 一对一)
    deleted["project_sources"] = await ProjectSource.filter(project_id=project_id).delete()

    # 6. RunSourceSnapshot(任务运行时的源码快照, 一对多)
    deleted["run_source_snapshots"] = await RunSourceSnapshot.filter(project_id=project_id).delete()

    # 7. P1-DB-04 补漏 - CrawlBatch(project_id 逻辑外键, 一对多)
    deleted["crawl_batches"] = await CrawlBatch.filter(project_id=project_id).delete()

    # 8. 项目本体
    await Project.filter(id=project_id).delete()


__all__ = ["delete_project_cascade"]
