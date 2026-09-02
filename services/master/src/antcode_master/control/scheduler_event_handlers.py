"""Business handlers for scheduler outbox events."""

from __future__ import annotations

from antcode_core.infrastructure.redis.client import get_redis_client
from antcode_core.infrastructure.redis.control_plane import redis_namespace
from loguru import logger


async def dispatch_special_event(data: dict, event_type: str) -> bool:
    if event_type == "crawl_project_cleanup":
        await cleanup_crawl_project(data)
        return True
    if event_type == "spider_storage_cleanup":
        await cleanup_spider_storage(data)
        return True
    if event_type == "task_logs_purge":
        await purge_task_logs(data)
        return True
    if not event_type.startswith("batch_"):
        return False
    await _dispatch_batch_event(data, event_type)
    return True


async def _dispatch_batch_event(data: dict, event_type: str) -> None:
    batch_id = data.get("batch_id")
    if not batch_id:
        raise ValueError(f"批次事件缺 batch_id: {event_type}")
    from antcode_core.application.services.crawl.batch_dispatcher_service import (
        crawl_batch_dispatcher_service,
    )

    await crawl_batch_dispatcher_service.handle_batch_event(event_type, str(batch_id))


async def cleanup_crawl_project(data: dict) -> None:
    from antcode_core.application.services.crawl.project_redis_cleanup import (
        CrawlProjectCleanupRequest,
        crawl_project_redis_cleanup,
    )

    project_id = data.get("project_id")
    batch_ids = data.get("batch_ids")
    if not isinstance(project_id, str) or not isinstance(batch_ids, list):
        raise ValueError("crawl_project_cleanup payload 格式非法")
    if not all(isinstance(batch_id, str) for batch_id in batch_ids):
        raise ValueError("crawl_project_cleanup batch_ids 格式非法")
    request = CrawlProjectCleanupRequest(project_id, tuple(batch_ids))
    report = await crawl_project_redis_cleanup.cleanup(request)
    logger.info(
        "outbox 驱动 Crawl 项目清理完成: project={} batches={}",
        report.project_id,
        report.batch_count,
    )


async def cleanup_spider_storage(data: dict) -> None:
    from antcode_core.application.services.crawl.spider_storage_cleanup import (
        SpiderStorageCleanupService,
    )
    from antcode_core.infrastructure.redis.keys import RedisKeys

    run_ids = data.get("run_ids")
    project_id = str(data.get("project_id") or "")
    if not isinstance(run_ids, list) or not all(isinstance(run_id, str) for run_id in run_ids):
        raise ValueError("spider_storage_cleanup run_ids 格式非法")
    redis = await get_redis_client()
    cleaner = SpiderStorageCleanupService(redis, RedisKeys(namespace=redis_namespace()))
    await cleaner.delete_runs(run_ids, project_id)


async def purge_task_logs(data: dict) -> None:
    from antcode_core.application.services.logs.task_log_run_guard import (
        purge_task_logs_for_runs,
    )

    run_ids = data.get("run_ids")
    if not isinstance(run_ids, list) or not all(isinstance(run_id, str) for run_id in run_ids):
        raise ValueError("task_logs_purge run_ids 格式非法")
    if not run_ids:
        return
    deleted = await purge_task_logs_for_runs(run_ids)
    logger.info("outbox 驱动 task_logs_purge 完成: run_batch={} deleted={}", len(run_ids), deleted)


__all__ = [
    "cleanup_crawl_project",
    "cleanup_spider_storage",
    "dispatch_special_event",
    "purge_task_logs",
]
