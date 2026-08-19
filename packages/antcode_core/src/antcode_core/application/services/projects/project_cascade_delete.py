"""项目级联删除（单事务原子 + durable Redis/日志补偿）。

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

作用域锁定与"是否还有在途执行"的判定见 ``project_delete_scope``：两族 run
（计划任务 run / 爬取批次 run）都会被检查，任一在途即拒绝删除。

**已知残留**：批次 run 的 ``TaskRun`` 行不随项目删除被清掉。它们的批次身份埋在
``result_data`` JSON 里且无表达式索引，按项目批量删只能扫遍哨兵族全表——那会在
持有 Project/Task/CrawlBatch 行锁的事务里拖住调度器。根治需先给
``task_executions`` 加一列带索引的 ``crawl_batch_id``，属独立的 schema 变更。
"""

from typing import cast

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.application.services.projects.project_delete_scope import (
    ProjectDeleteScope,
    lock_project_scope,
)
from antcode_core.domain.models.project import Project, ProjectCode, ProjectFile, ProjectRule
from antcode_core.domain.models.task import Task
from antcode_core.domain.models.task_run import TaskRun

_CLEANUP_BATCH_SIZE = 200


async def delete_project_cascade(project_id) -> dict:
    """级联删除项目及其相关数据，返回各表删除计数。"""
    deleted = {
        "tasks": 0,
        "executions": 0,
        "lease_generations": 0,
        "task_logs": 0,
        "details": 0,
        "runtime_bindings": 0,
        "project_sources": 0,
        "run_source_snapshots": 0,
        "crawl_batches": 0,
        "crawl_redis_cleanup_deferred": 0,
    }

    try:
        async with in_transaction() as conn:
            scope = await lock_project_scope(conn, project_id)
            cleanup_run_ids = await _delete_tasks_and_runs(conn, scope, deleted)
            await _publish_task_changed_events(conn, list(scope.task_ids))
            await _publish_crawl_project_cleanup_event(
                conn=conn,
                project_id=project_id,
                project_public_id=scope.project_public_id,
                batch_ids=scope.batch_ids,
            )
            await _delete_project_relations(conn, project_id, deleted)
            await _publish_task_logs_purge_events(conn, project_id, cleanup_run_ids)

        await _run_post_commit_cleanup(
            project_public_id=scope.project_public_id,
            batch_ids=scope.batch_ids,
            run_ids=cleanup_run_ids,
            deleted=deleted,
        )
        logger.info(f"级联删除项目 {project_id}: {deleted}")
        return deleted
    except Exception as e:
        logger.error(f"级联删除项目 {project_id} 失败: {e}")
        raise


async def _delete_tasks_and_runs(conn, scope: ProjectDeleteScope, deleted: dict) -> list[str]:
    """锁定校验后删除任务/执行记录/事务内日志。

    仅覆盖计划任务 run。批次 run 的 ``crawl_batch_id`` 埋在无索引的 JSON 里，
    按项目批量删需要先做 schema 变更（见模块文档），此处不做全表扫描。
    """
    from antcode_core.application.services.crawl.spider_storage_cleanup import (
        iter_cleanup_run_batches,
    )
    from antcode_core.application.services.scheduler.outbox_service import (
        scheduler_outbox_service,
    )
    from antcode_core.domain.models import TaskLog, TaskRunLeaseGeneration

    project_id = scope.project_id
    task_ids = list(scope.task_ids)

    # 1. 任务 → 执行记录(先删 child 避免悬挂)
    cleanup_run_ids: list[str] = []
    if task_ids:
        run_ids = await TaskRun.filter(task_id__in=list(task_ids)).using_db(conn).values_list("run_id", flat=True)
        cleanup_run_ids = [str(run_id) for run_id in run_ids if run_id]
        from antcode_core.application.services.workers.run_settlement_guard import (
            ensure_runs_settled,
        )

        await ensure_runs_settled(cleanup_run_ids)
        for run_batch in iter_cleanup_run_batches(cast(list[str], run_ids)):
            deleted["task_logs"] += await TaskLog.filter(run_id__in=list(run_batch)).using_db(conn).delete()
            deleted["lease_generations"] += (
                await TaskRunLeaseGeneration.filter(run_id__in=list(run_batch)).using_db(conn).delete()
            )
            await scheduler_outbox_service.enqueue(
                event_type="spider_storage_cleanup",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={"run_ids": list(run_batch), "project_id": scope.project_public_id},
                connection=conn,
            )
        deleted["executions"] = await TaskRun.filter(task_id__in=list(task_ids)).using_db(conn).delete()

    # 2. 任务本体
    deleted["tasks"] = await Task.filter(project_id=project_id).using_db(conn).delete()
    return cleanup_run_ids


async def _publish_crawl_project_cleanup_event(
    *,
    conn,
    project_id,
    project_public_id: str,
    batch_ids: tuple[str, ...],
) -> None:
    """把项目删除与 Redis 清理意图原子提交。"""
    from antcode_core.application.services.scheduler.outbox_service import (
        scheduler_outbox_service,
    )

    await scheduler_outbox_service.enqueue(
        event_type="crawl_project_cleanup",
        aggregate_type="project",
        aggregate_id=project_id,
        payload={"project_id": project_public_id, "batch_ids": list(batch_ids)},
        connection=conn,
    )


async def _run_post_commit_cleanup(
    *,
    project_public_id: str,
    batch_ids: tuple[str, ...],
    run_ids: list[str],
    deleted: dict,
) -> None:
    """同步缩短残留窗口；失败由事务内 outbox 明确补偿。"""
    from antcode_core.application.services.crawl.project_redis_cleanup import (
        CrawlProjectCleanupRequest,
        crawl_project_redis_cleanup,
    )
    from antcode_core.application.services.logs.task_log_run_guard import (
        purge_task_logs_for_runs,
    )

    request = CrawlProjectCleanupRequest(project_public_id, batch_ids)
    try:
        await crawl_project_redis_cleanup.cleanup(request)
    except Exception as exc:  # noqa: BLE001 - 事务内 outbox 将显式重试
        deleted["crawl_redis_cleanup_deferred"] = 1
        logger.warning("Crawl Redis 同步清理失败，等待 outbox 重试: project={} err={}", project_public_id, exc)
    try:
        deleted["task_logs"] += await purge_task_logs_for_runs(run_ids)
    except Exception as exc:  # noqa: BLE001 - 事务内 outbox 将显式重试
        logger.warning("同步 purge_task_logs 失败，等待 outbox 重试: project={} err={}", project_public_id, exc)


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
    for i in range(0, len(run_ids), _CLEANUP_BATCH_SIZE):
        batch = run_ids[i : i + _CLEANUP_BATCH_SIZE]
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


async def _delete_project_relations(conn, project_id, deleted: dict) -> None:
    """删除项目详情/绑定/来源/快照/批次与项目本体（事务内顺序执行）。"""
    from antcode_core.domain.models import (
        ProjectRuntimeBinding,
        ProjectSource,
        RunSourceSnapshot,
    )
    from antcode_core.domain.models.crawl import CrawlBatch

    # 3. 项目详情(file / rule / code 至多命中其一)；顺序执行避免共享连接并发冲突
    d_file = await ProjectFile.filter(project_id=project_id).using_db(conn).delete()
    d_rule = await ProjectRule.filter(project_id=project_id).using_db(conn).delete()
    d_code = await ProjectCode.filter(project_id=project_id).using_db(conn).delete()
    deleted["details"] = d_file + d_rule + d_code

    # 4. 运行时绑定
    deleted["runtime_bindings"] = await ProjectRuntimeBinding.filter(project_id=project_id).using_db(conn).delete()

    # 5. ProjectSource(Git 源配置, 一对一)
    deleted["project_sources"] = await ProjectSource.filter(project_id=project_id).using_db(conn).delete()

    # 6. RunSourceSnapshot(任务运行时的源码快照, 一对多)
    deleted["run_source_snapshots"] = await RunSourceSnapshot.filter(project_id=project_id).using_db(conn).delete()

    # 7. P1-DB-04 补漏 - CrawlBatch(project_id 逻辑外键, 一对多)
    deleted["crawl_batches"] = await CrawlBatch.filter(project_id=project_id).using_db(conn).delete()

    # 8. 项目本体
    await Project.filter(id=project_id).using_db(conn).delete()


__all__ = ["delete_project_cascade"]
