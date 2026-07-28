"""Retry statistics use run relationships instead of cumulative counters."""

from types import SimpleNamespace

import pytest
from antcode_core.application.services.scheduler.retry_statistics import build_retry_stats
from antcode_core.domain.models.enums import TaskStatus

_TASK_ID = 7
_TWO_EXECUTIONS = 2
_TWO_RETRIES = 2
_FULL_PERCENT = 100.0
_HALF_PERCENT = 50.0


def _run(run_id: str, *, source: str | None = None, retry_count: int = 0, status=TaskStatus.FAILED):
    result_data = {"retry_source_run_id": source} if source else {}
    return SimpleNamespace(
        run_id=run_id,
        retry_count=retry_count,
        result_data=result_data,
        status=status,
    )


def test_automatic_retry_is_not_counted_on_both_source_and_child() -> None:
    source = _run("source", retry_count=1)
    child = _run("child", source="source", retry_count=1, status=TaskStatus.SUCCESS)

    stats = build_retry_stats(_TASK_ID, [source, child])

    assert stats["total_executions"] == _TWO_EXECUTIONS
    assert stats["retried_executions"] == 1
    assert stats["total_retries"] == 1
    assert stats["retry_success_count"] == 1
    assert stats["retry_success_rate"] == _FULL_PERCENT


def test_multi_generation_chain_counts_attempts_and_one_root() -> None:
    source = _run("source", retry_count=1)
    retry_one = _run("retry-1", source="source", retry_count=2)
    retry_two = _run("retry-2", source="retry-1", retry_count=2, status=TaskStatus.SUCCESS)

    stats = build_retry_stats(_TASK_ID, [source, retry_one, retry_two])

    assert stats["total_retries"] == _TWO_RETRIES
    assert stats["retry_success_count"] == 1
    assert stats["retry_success_rate"] == _HALF_PERCENT
    assert stats["avg_retries_per_execution"] == float(_TWO_RETRIES)


def test_manual_retry_with_zero_counter_remains_visible() -> None:
    stats = build_retry_stats(
        _TASK_ID,
        [
            _run("source"),
            _run("manual", source="source", status=TaskStatus.SUCCESS),
        ],
    )

    assert stats["total_retries"] == 1
    assert stats["retry_success_count"] == 1


def test_corrupt_retry_cycle_is_rejected() -> None:
    first = _run("first", source="second")
    second = _run("second", source="first")

    with pytest.raises(RuntimeError, match="存在循环"):
        build_retry_stats(_TASK_ID, [first, second])
