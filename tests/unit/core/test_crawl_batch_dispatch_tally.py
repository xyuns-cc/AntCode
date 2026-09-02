"""批次派发日志的计数口径必须与真实结果一致。

走查实测：两个 seed 全部直派失败、只是进了补派队列，日志却打
``dispatched=1 failed=0``——运维扫这行会判定"派发正常"。这里用真实 loguru
sink 抓那一行，断言它既不把补派计成 dispatched，也不再是 INFO。
"""

from __future__ import annotations

from collections import Counter

import pytest
from antcode_core.application.services.crawl.batch_dispatch_state import SeedDispatchOutcome
from antcode_core.application.services.crawl.batch_dispatcher_service import (
    CrawlBatchDispatcherService,
)
from loguru import logger

SEED_TOTAL = 2


@pytest.fixture
def dispatch_log_lines():
    captured: list[tuple[str, str]] = []
    sink_id = logger.add(lambda message: captured.append((message.record["level"].name, message.record["message"])))
    try:
        yield captured
    finally:
        logger.remove(sink_id)


def _tally(**counts: int) -> Counter[SeedDispatchOutcome]:
    return Counter({SeedDispatchOutcome(name): value for name, value in counts.items()})


def _dispatch_summary(lines: list[tuple[str, str]]) -> tuple[str, str]:
    matches = [entry for entry in lines if entry[1].startswith("batch 派发结束")]
    assert len(matches) == 1, f"应恰好有 1 行派发汇总, 实际 {len(matches)}"
    return matches[0]


def test_redispatch_enqueued_is_not_counted_as_dispatched(dispatch_log_lines) -> None:
    tally = _tally(redispatch_enqueued=SEED_TOTAL)

    CrawlBatchDispatcherService._log_dispatch_tally("batch-1", tally, total=SEED_TOTAL)

    level, message = _dispatch_summary(dispatch_log_lines)
    assert "dispatched=0" in message
    assert "redispatch_enqueued=2" in message
    assert "failed=0" in message
    # 没有一个 seed 真正派出去，这行不能是 INFO 的"一切正常"。
    assert level == "WARNING"


def test_hard_failure_is_reported_as_failed(dispatch_log_lines) -> None:
    tally = _tally(dispatched=1, failed=1)

    CrawlBatchDispatcherService._log_dispatch_tally("batch-1", tally, total=SEED_TOTAL)

    level, message = _dispatch_summary(dispatch_log_lines)
    assert "dispatched=1" in message
    assert "failed=1" in message
    assert level == "WARNING"


def test_fully_successful_dispatch_stays_informational(dispatch_log_lines) -> None:
    tally = _tally(dispatched=SEED_TOTAL)

    CrawlBatchDispatcherService._log_dispatch_tally("batch-1", tally, total=SEED_TOTAL)

    level, message = _dispatch_summary(dispatch_log_lines)
    assert "dispatched=2" in message
    assert "already_dispatched=0 redispatch_enqueued=0 deferred=0 failed=0" in message
    assert level == "INFO"
