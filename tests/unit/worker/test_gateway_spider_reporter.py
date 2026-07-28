"""Gateway Spider reporter acknowledgement and buffer tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import pytest
from antcode_worker.plugins.spider.data.gateway_reporter import GatewayDataReporter
from antcode_worker.plugins.spider.data.models import SpiderDataItem

TEST_FLUSH_INTERVAL_SECONDS = 3600.0


@dataclass
class ScriptedGatewayClient:
    item_results: deque[bool] = field(default_factory=deque)
    meta_results: deque[bool] = field(default_factory=deque)
    item_calls: list[list[dict[str, Any]]] = field(default_factory=list)
    meta_calls: list[dict[str, Any]] = field(default_factory=list)

    async def report_spider_data(
        self,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> bool:
        assert run_id == "run-1"
        self.item_calls.append(items)
        return self.item_results.popleft() if self.item_results else True

    async def update_spider_meta(
        self,
        run_id: str,
        meta: dict[str, Any],
    ) -> bool:
        assert run_id == "run-1"
        self.meta_calls.append(meta)
        return self.meta_results.popleft() if self.meta_results else True


def _reporter(client: ScriptedGatewayClient, *, batch_size: int = 2) -> GatewayDataReporter:
    return GatewayDataReporter(
        client,
        run_id="run-1",
        project_id="project-1",
        spider_name="spider",
        batch_size=batch_size,
        flush_interval=TEST_FLUSH_INTERVAL_SECONDS,
    )


def _item(value: int) -> SpiderDataItem:
    return SpiderDataItem(
        run_id="run-1",
        project_id="project-1",
        spider_name="spider",
        data={"value": value},
    )


@pytest.mark.asyncio
async def test_acknowledged_batch_is_removed_without_mutating_inputs() -> None:
    client = ScriptedGatewayClient()
    reporter = _reporter(client)
    first = _item(1)
    second = _item(2)
    await reporter.start()

    assert await reporter.report_batch([first, second]) is True
    assert first.sequence == 0
    assert second.sequence == 0
    assert [row["sequence"] for row in client.item_calls[0]] == ["1", "2"]
    assert await reporter.flush() is True
    assert len(client.item_calls) == 1

    await reporter.stop()


@pytest.mark.asyncio
async def test_rejected_batch_remains_buffered_for_retry() -> None:
    client = ScriptedGatewayClient(item_results=deque([False, True]))
    reporter = _reporter(client, batch_size=1)
    await reporter.start()

    assert await reporter.report_item(_item(1)) is False
    first_attempt = client.item_calls[0]
    assert await reporter.flush() is True
    assert client.item_calls == [first_attempt, first_attempt]

    await reporter.stop()


@pytest.mark.asyncio
async def test_finalize_waits_for_items_and_reports_complete_meta() -> None:
    client = ScriptedGatewayClient()
    reporter = _reporter(client, batch_size=10)
    await reporter.start()
    assert await reporter.report_item(_item(1)) is True

    finalized = await reporter.finalize(
        "run-1",
        status="failed",
        items_count=1,
        pages_count=3,
        errors_count=2,
        duration_ms=12.5,
        errors=["network", "parse"],
    )

    assert finalized is True
    assert len(client.item_calls) == 1
    final_meta = client.meta_calls[-1]
    assert final_meta["status"] == "failed"
    assert final_meta["items_count"] == "1"
    assert final_meta["pages_count"] == "3"
    assert final_meta["errors_count"] == "2"
    assert final_meta["duration_ms"] == "12.5"
    assert final_meta["errors"] == '["network", "parse"]'
    assert final_meta["finished_at"]

    await reporter.stop()


@pytest.mark.asyncio
async def test_finalize_does_not_publish_meta_when_flush_fails() -> None:
    client = ScriptedGatewayClient(item_results=deque([False, True]))
    reporter = _reporter(client, batch_size=10)
    await reporter.start()
    await reporter.report_item(_item(1))

    finalized = await reporter.finalize(
        "run-1",
        status="completed",
        items_count=1,
        pages_count=1,
        errors_count=0,
        duration_ms=1.0,
    )

    assert finalized is False
    assert len(client.meta_calls) == 1
    assert await reporter.flush() is True
    await reporter.stop()


@pytest.mark.asyncio
async def test_start_exposes_initial_meta_rejection() -> None:
    client = ScriptedGatewayClient(meta_results=deque([False]))
    reporter = _reporter(client)

    with pytest.raises(RuntimeError, match="初始元数据上报失败"):
        await reporter.start()


@pytest.mark.asyncio
async def test_stop_can_retry_a_failed_final_flush() -> None:
    client = ScriptedGatewayClient(item_results=deque([False, True]))
    reporter = _reporter(client, batch_size=10)
    await reporter.start()
    await reporter.report_item(_item(1))

    with pytest.raises(RuntimeError, match="最终刷写失败"):
        await reporter.stop()
    first_attempt = client.item_calls[0]

    await reporter.stop()
    assert client.item_calls == [first_attempt, first_attempt]


@pytest.mark.asyncio
async def test_finalize_returns_false_when_final_meta_is_rejected() -> None:
    client = ScriptedGatewayClient(meta_results=deque([True, False]))
    reporter = _reporter(client)
    await reporter.start()

    assert not await reporter.finalize(
        "run-1",
        status="completed",
        items_count=0,
        pages_count=0,
        errors_count=0,
        duration_ms=0.0,
    )
    await reporter.stop()
