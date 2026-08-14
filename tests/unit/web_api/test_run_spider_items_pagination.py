from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import runs


@pytest.mark.asyncio
async def test_spider_items_exposes_next_page_without_consuming_lookahead(monkeypatch) -> None:
    monkeypatch.setattr(
        runs.scheduler_service,
        "get_execution_with_permission",
        AsyncMock(return_value=SimpleNamespace(run_id="run-1")),
    )
    read = AsyncMock(
        return_value=[
            (b"1-0", {b"data": b'{"value":1}'}),
            (b"2-0", {b"data": b'{"value":2}'}),
            (b"3-0", {b"data": b'{"value":3}'}),
        ]
    )
    monkeypatch.setattr(runs, "_read_spider_stream", read)

    response = await runs.list_spider_items(
        "run-1",
        SimpleNamespace(user_id=1),
        start_id="0",
        count=2,
    )

    assert response.data == {
        "items": [
            {"_id": "1-0", "data": {"value": 1}},
            {"_id": "2-0", "data": {"value": 2}},
        ],
        "last_id": "2-0",
        "count": 2,
        "has_more": True,
    }
    read.assert_awaited_once_with("run-1", "0", 3)
