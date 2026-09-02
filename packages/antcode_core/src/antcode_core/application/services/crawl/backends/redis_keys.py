"""Namespaced Redis keys for Crawl aggregates."""

from __future__ import annotations

from antcode_core.infrastructure.redis.control_plane import redis_namespace


def crawl_batch_tag(
    project_id: str,
    batch_id: str,
    namespace: str | None = None,
) -> str:
    """Return the Cluster hash tag shared by one batch's progress keys."""
    return f"{{{redis_namespace(namespace)}:crawl:{project_id}:{batch_id}}}"


def crawl_progress_key(
    project_id: str,
    batch_id: str,
    namespace: str | None = None,
) -> str:
    return f"{crawl_batch_tag(project_id, batch_id, namespace)}:progress"


def crawl_checkpoint_key(
    project_id: str,
    batch_id: str,
    namespace: str | None = None,
) -> str:
    return f"{crawl_batch_tag(project_id, batch_id, namespace)}:checkpoint"


def crawl_workers_key(
    project_id: str,
    batch_id: str,
    namespace: str | None = None,
) -> str:
    return f"{crawl_batch_tag(project_id, batch_id, namespace)}:workers"


def crawl_cancel_fence_key(
    project_id: str,
    batch_id: str,
    namespace: str | None = None,
) -> str:
    return f"{crawl_batch_tag(project_id, batch_id, namespace)}:cancelled"


def crawl_worker_registry_key(namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:crawl:workers:registry"


def crawl_batch_workers_key(batch_id: str, namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:crawl:batch:{batch_id}:workers"


def crawl_test_result_key(batch_id: str, namespace: str | None = None) -> str:
    return f"{redis_namespace(namespace)}:crawl:test:result:{batch_id}"


__all__ = [
    "crawl_batch_tag",
    "crawl_batch_workers_key",
    "crawl_checkpoint_key",
    "crawl_cancel_fence_key",
    "crawl_progress_key",
    "crawl_test_result_key",
    "crawl_worker_registry_key",
    "crawl_workers_key",
]
