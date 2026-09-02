"""批次级 max_concurrency / max_pages 必须是批次总量，不是每个 seed 的额度。

每个 seed URL 被派成一条独立 rule 任务，worker 为它起一个独立 Scrapy 进程，
进程的 ``CONCURRENT_REQUESTS`` 直接取 rule dict 的 ``concurrent_requests``。
逐字复制批次额度 = 把上限乘以 seed 数。这里从 batch 字段一路量到 Scrapy
settings，断言"同时在跑的 seed 数 × 每 seed 并发 ≤ 批次额度"。
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.crawl.batch_dispatch_state import SeedDispatchOutcome
from antcode_core.application.services.crawl.batch_dispatcher_service import CrawlBatchDispatcherService
from antcode_core.application.services.crawl.batch_rule_options import (
    batch_rule_overrides,
    batch_seed_slots,
)
from antcode_scrapy.settings import build_settings

CONCURRENCY_QUOTA = 50
PAGE_QUOTA = 10_000
MANY_SEEDS = 100
FEW_SEEDS = 10
TINY_QUOTA = 3
RETRIES = 3
TIMEOUT_SECONDS = 30
MAX_DEPTH = 2


def _batch(*, max_concurrency: int = CONCURRENCY_QUOTA, max_pages: int = PAGE_QUOTA) -> SimpleNamespace:
    return SimpleNamespace(
        public_id="batch-1",
        max_concurrency=max_concurrency,
        max_pages=max_pages,
        max_retries=RETRIES,
        timeout=TIMEOUT_SECONDS,
        max_depth=MAX_DEPTH,
        request_delay=0.5,
    )


def _live_concurrency(batch: SimpleNamespace, seed_count: int) -> int:
    """走真实通路量出"同时在跑的总并发"：Scrapy settings × 并行 seed 数。"""
    rule_dict = batch_rule_overrides(batch, seed_count=seed_count)
    per_seed = build_settings(rule_dict)["CONCURRENT_REQUESTS"]
    return per_seed * batch_seed_slots(batch, seed_count=seed_count)


def test_batch_concurrency_holds_when_seeds_outnumber_the_quota() -> None:
    batch = _batch()

    # 平分地板为 1（并发是速率，不能取 0），所以只能靠并行 seed 数收口。
    assert batch_rule_overrides(batch, seed_count=MANY_SEEDS)["concurrent_requests"] == 1
    assert batch_seed_slots(batch, seed_count=MANY_SEEDS) == CONCURRENCY_QUOTA
    assert _live_concurrency(batch, MANY_SEEDS) == CONCURRENCY_QUOTA


def test_batch_concurrency_holds_when_the_quota_divides_evenly() -> None:
    batch = _batch()

    assert batch_rule_overrides(batch, seed_count=FEW_SEEDS)["concurrent_requests"] == CONCURRENCY_QUOTA // FEW_SEEDS
    # 整除时槽位放开全部 seed，没有被无谓地压慢。
    assert batch_seed_slots(batch, seed_count=FEW_SEEDS) == FEW_SEEDS
    assert _live_concurrency(batch, FEW_SEEDS) == CONCURRENCY_QUOTA


def test_quota_smaller_than_one_per_seed_still_never_exceeds_the_batch_limit() -> None:
    batch = _batch(max_concurrency=TINY_QUOTA)

    assert batch_seed_slots(batch, seed_count=MANY_SEEDS) == TINY_QUOTA
    assert _live_concurrency(batch, MANY_SEEDS) == TINY_QUOTA


def test_max_pages_is_split_because_it_is_a_batch_total() -> None:
    batch = _batch()

    per_seed = batch_rule_overrides(batch, seed_count=MANY_SEEDS)["max_pages"]

    assert per_seed == PAGE_QUOTA // MANY_SEEDS
    assert per_seed * MANY_SEEDS == PAGE_QUOTA


def test_per_request_fields_are_not_split() -> None:
    """retry/timeout/depth 语义是"每次请求"，平分它们会改语义。"""
    overrides = batch_rule_overrides(_batch(), seed_count=MANY_SEEDS)

    assert overrides["retry_count"] == RETRIES
    assert overrides["timeout"] == TIMEOUT_SECONDS
    assert overrides["max_depth"] == MAX_DEPTH


@pytest.mark.asyncio
async def test_dispatch_admits_only_as_many_seeds_as_the_quota_allows(monkeypatch) -> None:
    service = CrawlBatchDispatcherService()
    batch = _batch()
    urls = [f"https://seed.test/{index}" for index in range(MANY_SEEDS)]
    single = AsyncMock(return_value=SeedDispatchOutcome.DISPATCHED)
    monkeypatch.setattr(service, "_active_run_ids_for_batch", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_dispatch_single_url", single)

    tally = await service._dispatch_pending_urls(
        batch,
        SimpleNamespace(public_id="project-1"),
        SimpleNamespace(),
        urls=urls,
        seed_count=MANY_SEEDS,
    )

    assert tally[SeedDispatchOutcome.DISPATCHED] == CONCURRENCY_QUOTA
    assert tally[SeedDispatchOutcome.DEFERRED] == MANY_SEEDS - CONCURRENCY_QUOTA
    assert single.await_count == CONCURRENCY_QUOTA


@pytest.mark.asyncio
async def test_dispatch_budget_counts_seeds_already_occupying_a_slot(monkeypatch) -> None:
    """已在跑的 run 占额度：不减掉它们，追派就会把并发叠加上去。"""
    service = CrawlBatchDispatcherService()
    batch = _batch()
    running = [f"run-{index}" for index in range(CONCURRENCY_QUOTA - FEW_SEEDS)]
    urls = [f"https://seed.test/{index}" for index in range(MANY_SEEDS)]
    single = AsyncMock(return_value=SeedDispatchOutcome.DISPATCHED)
    monkeypatch.setattr(service, "_active_run_ids_for_batch", AsyncMock(return_value=running))
    monkeypatch.setattr(service, "_dispatch_single_url", single)

    tally = await service._dispatch_pending_urls(
        batch,
        SimpleNamespace(public_id="project-1"),
        SimpleNamespace(),
        urls=urls,
        seed_count=MANY_SEEDS,
    )

    assert tally[SeedDispatchOutcome.DISPATCHED] == FEW_SEEDS
    assert tally[SeedDispatchOutcome.DEFERRED] == MANY_SEEDS - FEW_SEEDS


@pytest.mark.asyncio
async def test_dispatch_defers_everything_when_the_quota_is_already_full(monkeypatch) -> None:
    service = CrawlBatchDispatcherService()
    batch = _batch()
    running = [f"run-{index}" for index in range(CONCURRENCY_QUOTA)]
    urls = [f"https://seed.test/{index}" for index in range(MANY_SEEDS)]
    single = AsyncMock(return_value=SeedDispatchOutcome.DISPATCHED)
    monkeypatch.setattr(service, "_active_run_ids_for_batch", AsyncMock(return_value=running))
    monkeypatch.setattr(service, "_dispatch_single_url", single)

    tally = await service._dispatch_pending_urls(
        batch,
        SimpleNamespace(public_id="project-1"),
        SimpleNamespace(),
        urls=urls,
        seed_count=MANY_SEEDS,
    )

    assert tally == Counter({SeedDispatchOutcome.DEFERRED: MANY_SEEDS})
    single.assert_not_awaited()
