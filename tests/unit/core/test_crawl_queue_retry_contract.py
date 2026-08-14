"""Crawl queue service retry and acknowledgement contracts."""

from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.crawl.queue_service import CrawlQueueService, CrawlTask
from antcode_core.domain.models.crawl import CrawlTaskStatus
from antcode_core.domain.models.enums import Priority

PROJECT_ID = "project-1"
SOURCE_MESSAGE_ID = "1-0"
MIGRATED_MESSAGE_ID = "2-0"
MAX_RETRIES = 3
INITIAL_RETRY_COUNT = 1
PROVIDED_RETRY_COUNT = INITIAL_RETRY_COUNT + 1
FINAL_FAILURE_COUNT = MAX_RETRIES + 1


def _running_task(retry_count: int) -> CrawlTask:
    return CrawlTask(
        msg_id=SOURCE_MESSAGE_ID,
        url="https://example.test",
        project_id=PROJECT_ID,
        priority=Priority.NORMAL,
        retry_count=retry_count,
        status=CrawlTaskStatus.RUNNING,
    )


@pytest.mark.asyncio
async def test_retry_atomically_settles_the_provided_retry_count() -> None:
    backend = AsyncMock()
    backend.requeue_claimed.return_value = MIGRATED_MESSAGE_ID
    service = CrawlQueueService(backend=backend)
    task = _running_task(PROVIDED_RETRY_COUNT)

    assert await service.retry_task(PROJECT_ID, task) == (True, MIGRATED_MESSAGE_ID)

    queued = backend.requeue_claimed.await_args.args[1]
    assert queued.msg_id == SOURCE_MESSAGE_ID
    assert queued.priority == Priority.NORMAL
    assert queued.retry_count == PROVIDED_RETRY_COUNT
    assert task.retry_count == PROVIDED_RETRY_COUNT
    backend.enqueue.assert_not_awaited()
    backend.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_task_retry_increments_an_immutable_copy_before_settlement() -> None:
    backend = AsyncMock()
    backend.requeue_claimed.return_value = MIGRATED_MESSAGE_ID
    service = CrawlQueueService(backend=backend, max_retries=MAX_RETRIES)
    task = _running_task(INITIAL_RETRY_COUNT)

    result = await service.mark_task_retry(task)

    assert result.success is True
    assert result.task is not task
    assert result.task is not None
    assert result.task.retry_count == PROVIDED_RETRY_COUNT
    assert task.retry_count == INITIAL_RETRY_COUNT
    assert task.status == CrawlTaskStatus.RUNNING
    queued = backend.requeue_claimed.await_args.args[1]
    assert queued.retry_count == PROVIDED_RETRY_COUNT


@pytest.mark.asyncio
async def test_process_result_dead_letters_the_fourth_failure_with_count_four() -> None:
    backend = AsyncMock()
    backend.move_to_dead_letter.return_value = 1
    service = CrawlQueueService(backend=backend, max_retries=MAX_RETRIES)
    task = _running_task(MAX_RETRIES)

    result = await service.process_task_result(task, success=False)

    assert result.success is True
    [dead_lettered] = backend.move_to_dead_letter.await_args.args[1]
    assert dead_lettered.retry_count == FINAL_FAILURE_COUNT
    assert task.retry_count == MAX_RETRIES
    assert task.status == CrawlTaskStatus.RUNNING
    backend.requeue_claimed.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_priority_ack_keeps_identical_ids_separated() -> None:
    backend = AsyncMock()
    backend.ack.side_effect = [1, 1]
    service = CrawlQueueService(backend=backend)
    tasks = [
        CrawlTask(msg_id=SOURCE_MESSAGE_ID, priority=Priority.HIGH),
        CrawlTask(msg_id=SOURCE_MESSAGE_ID, priority=Priority.LOW),
    ]

    assert await service.ack_tasks_multi_priority(PROJECT_ID, tasks) == len(tasks)
    assert backend.ack.await_args_list[0].args == (PROJECT_ID, [SOURCE_MESSAGE_ID], Priority.HIGH)
    assert backend.ack.await_args_list[1].args == (PROJECT_ID, [SOURCE_MESSAGE_ID], Priority.LOW)
