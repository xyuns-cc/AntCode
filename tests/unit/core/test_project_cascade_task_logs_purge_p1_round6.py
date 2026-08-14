"""P1-round6 5.2 回归:项目级联删除的 task_logs_purge outbox durable cleanup。

审查文档 round6 5.2:
`Task/Project 删除在提交后 purge 日志和 Redis 数据; 崩溃后无 durable
cleanup, late writer 还能重建孤儿`。

Bug 场景:
- delete_project_cascade 事务提交成功 (TaskRun 已删)
- 事务后 purge_task_logs_for_runs 未开始就崩溃 (Master OOM / 部署)
- 遗留 task_logs 变成孤儿 (无对应 TaskRun 但仍存 PG)

修复:
- 事务内 enqueue task_logs_purge outbox 事件 (原子性: TaskRun 删除 +
  purge intent 一起入库)
- scheduler_event_loop._purge_task_logs 消费, 走同 advisory lock 幂等
  (重复消费 DELETE 无匹配行, 无副作用)
- 事务后同步 purge 保留为 best-effort 优化, 失败 warn 不阻塞 delete API

本测试锁死:
1. cleanup_run_ids 非空 → enqueue 至少一条 task_logs_purge
2. 分批 (batch_size=200), 超 200 个 run_id 会 enqueue 多条
3. cleanup_run_ids 为空 → 不 enqueue
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BATCH_SIZE = 200
_OVERFLOW_COUNT = 250
_EXPECTED_BATCHES = 2
_EXPECTED_PROJECT_ID = 42


@pytest.mark.asyncio
async def test_publish_task_logs_purge_events_batches_run_ids():
    """P1-round6 5.2: 200 batch_size 分批 enqueue。"""
    from antcode_core.application.services.projects.project_cascade_delete import (
        _publish_task_logs_purge_events,
    )

    enqueue_mock = AsyncMock()
    with patch(
        "antcode_core.application.services.scheduler.outbox_service.scheduler_outbox_service.enqueue",
        enqueue_mock,
    ):
        conn = MagicMock()
        run_ids = [f"r-{i}" for i in range(_OVERFLOW_COUNT)]
        await _publish_task_logs_purge_events(conn, project_id=_EXPECTED_PROJECT_ID, run_ids=run_ids)

    # 250 run → 2 批 (200 + 50)
    assert enqueue_mock.await_count == _EXPECTED_BATCHES
    first_call = enqueue_mock.await_args_list[0]
    assert first_call.kwargs["event_type"] == "task_logs_purge"
    assert first_call.kwargs["aggregate_type"] == "project"
    assert first_call.kwargs["aggregate_id"] == _EXPECTED_PROJECT_ID
    assert len(first_call.kwargs["payload"]["run_ids"]) == _BATCH_SIZE
    second_call = enqueue_mock.await_args_list[1]
    assert len(second_call.kwargs["payload"]["run_ids"]) == _OVERFLOW_COUNT - _BATCH_SIZE


@pytest.mark.asyncio
async def test_publish_task_logs_purge_events_empty_noop():
    """空 run_ids 不 enqueue 空事件。"""
    from antcode_core.application.services.projects.project_cascade_delete import (
        _publish_task_logs_purge_events,
    )

    enqueue_mock = AsyncMock()
    with patch(
        "antcode_core.application.services.scheduler.outbox_service.scheduler_outbox_service.enqueue",
        enqueue_mock,
    ):
        conn = MagicMock()
        await _publish_task_logs_purge_events(conn, project_id=1, run_ids=[])

    enqueue_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_task_logs_purge_events_payload_shape():
    """payload 结构与消费者(_purge_task_logs)期望一致。"""
    from antcode_core.application.services.projects.project_cascade_delete import (
        _publish_task_logs_purge_events,
    )

    enqueue_mock = AsyncMock()
    with patch(
        "antcode_core.application.services.scheduler.outbox_service.scheduler_outbox_service.enqueue",
        enqueue_mock,
    ):
        conn = MagicMock()
        await _publish_task_logs_purge_events(conn, project_id=7, run_ids=["run-a", "run-b"])

    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["project_id"] == "7"
    assert payload["run_ids"] == ["run-a", "run-b"]


@pytest.mark.asyncio
async def test_publish_crawl_project_cleanup_event_preserves_public_ids():
    from antcode_core.application.services.projects.project_cascade_delete import (
        _publish_crawl_project_cleanup_event,
    )

    enqueue_mock = AsyncMock()
    with patch(
        "antcode_core.application.services.scheduler.outbox_service.scheduler_outbox_service.enqueue",
        enqueue_mock,
    ):
        conn = MagicMock()
        await _publish_crawl_project_cleanup_event(
            conn=conn,
            project_id=7,
            project_public_id="project-public",
            batch_ids=("batch-a", "batch-b"),
        )

    kwargs = enqueue_mock.await_args.kwargs
    assert kwargs == {
        "event_type": "crawl_project_cleanup",
        "aggregate_type": "project",
        "aggregate_id": 7,
        "payload": {
            "project_id": "project-public",
            "batch_ids": ["batch-a", "batch-b"],
        },
        "connection": conn,
    }


@pytest.mark.asyncio
async def test_post_commit_crawl_failure_is_marked_for_durable_retry():
    from antcode_core.application.services.projects.project_cascade_delete import (
        _run_post_commit_cleanup,
    )

    deleted = {"task_logs": 0, "crawl_redis_cleanup_deferred": 0}
    with (
        patch(
            "antcode_core.application.services.crawl.project_redis_cleanup.crawl_project_redis_cleanup.cleanup",
            AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ),
        patch(
            "antcode_core.application.services.logs.task_log_run_guard.purge_task_logs_for_runs",
            AsyncMock(return_value=2),
        ),
    ):
        await _run_post_commit_cleanup(
            project_public_id="project-public",
            batch_ids=("batch-a",),
            run_ids=["run-a"],
            deleted=deleted,
        )

    assert deleted == {"task_logs": 2, "crawl_redis_cleanup_deferred": 1}
