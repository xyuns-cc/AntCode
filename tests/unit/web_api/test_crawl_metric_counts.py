from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from antcode_web_api.routes.v1 import crawl

TOTAL_BATCHES = 1_500
RUNNING_BATCHES = 1_200


@pytest.mark.asyncio
async def test_crawl_metrics_counts_running_batches_without_page_cap(monkeypatch) -> None:
    monkeypatch.setattr(crawl, "_verify_project_access", AsyncMock())
    monkeypatch.setattr(
        crawl.crawl_metrics_service,
        "collect_system_metrics",
        AsyncMock(
            return_value=SimpleNamespace(
                total_stream_length=1,
                total_pel_size=2,
                dedup_size=3,
                dead_letter_count=4,
                active_workers=5,
            )
        ),
    )
    count_batches = AsyncMock(side_effect=[TOTAL_BATCHES, RUNNING_BATCHES])
    monkeypatch.setattr(crawl, "count_batches", count_batches)

    response = await crawl.get_system_metrics("project-1", SimpleNamespace(user_id=1))

    assert response.data.total_batches == TOTAL_BATCHES
    assert response.data.running_batches == RUNNING_BATCHES
    assert count_batches.await_args_list == [call("project-1"), call("project-1", "running")]
