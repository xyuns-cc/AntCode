"""Database aggregates for Crawl batches."""

from __future__ import annotations

from antcode_core.domain.models.crawl import CrawlBatch
from antcode_core.domain.models.project import Project


async def count_batches(project_id: str, status: str | None = None) -> int:
    project = await Project.get_or_none(public_id=project_id).only("id")
    if not project:
        return 0
    query = CrawlBatch.filter(project_id=project.id)
    if status:
        query = query.filter(status=status)
    return await query.count()


__all__ = ["count_batches"]
