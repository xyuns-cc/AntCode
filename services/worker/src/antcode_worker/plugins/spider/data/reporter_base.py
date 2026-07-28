"""Spider data reporter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from antcode_worker.plugins.spider.data.models import SpiderDataItem, SpiderMeta


class SpiderDataReporter(ABC):
    """Common reporting contract for Direct and Gateway modes."""

    @abstractmethod
    async def start(self) -> None:
        """Start the reporter."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the reporter after flushing buffered data."""

    @abstractmethod
    async def report_item(self, item: SpiderDataItem) -> bool:
        """Report one item."""

    @abstractmethod
    async def report_batch(self, items: list[SpiderDataItem]) -> bool:
        """Report an item batch."""

    @abstractmethod
    async def update_meta(self, meta: SpiderMeta) -> bool:
        """Persist run metadata."""

    @abstractmethod
    async def finalize(
        self,
        run_id: str,
        *,
        status: str,
        items_count: int,
        pages_count: int,
        errors_count: int,
        duration_ms: float,
        errors: list[str] | None = None,
    ) -> bool:
        """Flush data and persist the final run state."""

    @abstractmethod
    async def flush(self) -> bool:
        """Flush buffered items."""


__all__ = ["SpiderDataReporter"]
