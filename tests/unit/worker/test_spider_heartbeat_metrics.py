import json
import math
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_worker.engine.engine import Engine
from antcode_worker.heartbeat.reporter import HeartbeatReporter
from antcode_worker.heartbeat.spider_stats import SpiderStatsAccumulator
from antcode_worker.heartbeat.system_metrics import SystemMetricsCollector

EXPECTED_REQUEST_COUNT = 5
EXPECTED_RUNNING_TASKS = 3
EXPECTED_MAX_SLOTS = 8
EXPECTED_ENV_COUNT = 6
EXPECTED_PROJECT_COUNT = 2
MAX_PROTO_INT64 = (1 << 63) - 1


class _StateManager:
    async def count_active(self) -> int:
        return 3


class _Transport:
    is_connected = True

    def __init__(self) -> None:
        self.heartbeat = None

    async def send_heartbeat(self, heartbeat):
        self.heartbeat = heartbeat
        return True

    async def reconnect(self) -> bool:
        return True


def _stats_payload() -> dict:
    return {
        "request_count": 5,
        "response_count": 4,
        "item_scraped_count": 2,
        "error_count": 1,
        "avg_latency_ms": 25.0,
        "status_codes": {"200": 3, "500": 1},
        "domain_stats": [
            {
                "domain": "example.com",
                "requests": 4,
                "successes": 3,
                "avg_latency_ms": 25.0,
            }
        ],
    }


def test_spider_stats_accumulator_validates_and_aggregates(tmp_path) -> None:
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps(_stats_payload()), encoding="utf-8")
    stats_file.chmod(0o600)
    accumulator = SpiderStatsAccumulator()

    accumulator.record_file(str(stats_file))
    snapshot = accumulator.snapshot()

    assert not stats_file.exists()
    assert snapshot is not None
    assert snapshot["request_count"] == EXPECTED_REQUEST_COUNT
    assert snapshot["requests_per_minute"] == float(EXPECTED_REQUEST_COUNT)
    assert snapshot["status_codes"] == {"200": 3, "500": 1}
    assert snapshot["domain_stats"] == [
        {
            "domain": "example.com",
            "reqs": 4,
            "successRate": 75.0,
            "latency": 25.0,
        }
    ]


def test_spider_stats_accumulator_rejects_invalid_counts(tmp_path) -> None:
    payload = _stats_payload()
    payload["request_count"] = -1
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps(payload), encoding="utf-8")
    stats_file.chmod(0o600)

    with pytest.raises(ValueError, match="request_count"):
        SpiderStatsAccumulator().record_file(str(stats_file))


def test_spider_stats_accumulator_rejects_oversized_sidecar(tmp_path) -> None:
    stats_file = tmp_path / "oversized.json"
    stats_file.write_bytes(b"{" + b" " * 16)
    stats_file.chmod(0o600)

    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        SpiderStatsAccumulator(max_file_bytes=8).record_file(str(stats_file))


@pytest.mark.parametrize(
    "case",
    [
        ("avg_latency_ms", math.nan, "finite and non-negative"),
        ("request_count", 1 << 63, "protobuf int64"),
        ("status_codes", {"invalid": 1}, "invalid literal"),
        ("status_codes", {99: 1}, "between 100 and 599"),
    ],
)
def test_spider_stats_accumulator_rejects_unencodable_values(tmp_path, case) -> None:
    field, value, message = case
    payload = _stats_payload()
    payload[field] = value
    stats_file = tmp_path / "invalid.json"
    stats_file.write_text(json.dumps(payload), encoding="utf-8")
    stats_file.chmod(0o600)

    with pytest.raises(ValueError, match=message):
        SpiderStatsAccumulator().record_file(str(stats_file))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is unavailable on this platform")
def test_spider_stats_accumulator_rejects_fifo_without_blocking(tmp_path) -> None:
    stats_file = tmp_path / "stats.pipe"
    os.mkfifo(stats_file, mode=0o600)

    with pytest.raises(PermissionError, match="regular file"):
        SpiderStatsAccumulator().record_file(str(stats_file))


def test_spider_stats_accumulator_rejects_cumulative_overflow_atomically(tmp_path) -> None:
    accumulator = SpiderStatsAccumulator()
    initial_payload = _stats_payload()
    initial_payload["request_count"] = MAX_PROTO_INT64
    initial_file = tmp_path / "initial.json"
    initial_file.write_text(json.dumps(initial_payload), encoding="utf-8")
    initial_file.chmod(0o600)
    accumulator.record_file(str(initial_file))
    initial_snapshot = accumulator.snapshot()

    overflow_file = tmp_path / "overflow.json"
    overflow_file.write_text(json.dumps(_stats_payload()), encoding="utf-8")
    overflow_file.chmod(0o600)

    with pytest.raises(ValueError, match="cumulative request_count"):
        accumulator.record_file(str(overflow_file))

    assert overflow_file.exists()
    assert accumulator.snapshot() == initial_snapshot


def test_spider_stats_accumulator_rejects_weighted_latency_overflow(tmp_path) -> None:
    payload = _stats_payload()
    payload["response_count"] = 2
    payload["avg_latency_ms"] = sys.float_info.max
    stats_file = tmp_path / "latency-overflow.json"
    stats_file.write_text(json.dumps(payload), encoding="utf-8")
    stats_file.chmod(0o600)
    accumulator = SpiderStatsAccumulator()

    with pytest.raises(ValueError, match="cumulative latency_weighted"):
        accumulator.record_file(str(stats_file))

    assert stats_file.exists()
    assert accumulator.snapshot() is None


@pytest.mark.asyncio
async def test_reporter_uses_live_running_count_and_spider_stats(monkeypatch, tmp_path) -> None:
    from antcode_worker.heartbeat import system_metrics

    monkeypatch.setattr(system_metrics, "HAS_PSUTIL", False)
    collector = SystemMetricsCollector(max_slots=8)
    collector.set_state_manager(_StateManager())
    collector.set_scheduler(SimpleNamespace(size=2))
    collector.set_env_count_provider(lambda: EXPECTED_ENV_COUNT)
    collector.record_task_executed("project-1")
    collector.record_task_executed("project-2")
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps(_stats_payload()), encoding="utf-8")
    stats_file.chmod(0o600)
    collector.record_spider_stats_file(str(stats_file))
    transport = _Transport()
    reporter = HeartbeatReporter(
        transport=transport,
        worker_id="worker-1",
        metrics_collector=collector,
        max_concurrent_tasks=8,
    )

    assert await reporter.send_heartbeat() is True

    assert transport.heartbeat.metrics.running_tasks == EXPECTED_RUNNING_TASKS
    assert transport.heartbeat.metrics.max_concurrent_tasks == EXPECTED_MAX_SLOTS
    assert transport.heartbeat.metrics.task_count == EXPECTED_PROJECT_COUNT
    assert transport.heartbeat.metrics.project_count == EXPECTED_PROJECT_COUNT
    assert transport.heartbeat.metrics.env_count == EXPECTED_ENV_COUNT
    assert transport.heartbeat.metrics.spider_stats.request_count == EXPECTED_REQUEST_COUNT
    assert transport.heartbeat.metrics.spider_stats.domain_stats[0]["domain"] == "example.com"


@pytest.mark.asyncio
async def test_engine_counts_new_execution_once() -> None:
    engine = Engine(transport=MagicMock(), executor=MagicMock())
    result = SimpleNamespace(run_id="run-1")
    engine._execute_task = AsyncMock(return_value=result)
    completed = MagicMock()
    engine.set_task_completed_recorder(completed)

    context = SimpleNamespace(run_id="run-1", project_id="project-1")
    returned = await engine._execute_or_resume_settlement(context, MagicMock())

    assert returned is result
    completed.assert_called_once_with("project-1")


@pytest.mark.asyncio
async def test_engine_does_not_recount_settlement_replay() -> None:
    engine = Engine(transport=MagicMock(), executor=MagicMock())
    result = SimpleNamespace(run_id="run-1")
    engine._state_manager.has_pending_settlement = AsyncMock(return_value=True)
    engine._state_manager.settlement_snapshot = AsyncMock(return_value=(result, False, ()))
    completed = MagicMock()
    engine.set_task_completed_recorder(completed)

    returned = await engine._execute_or_resume_settlement(SimpleNamespace(run_id="run-1"), MagicMock())

    assert returned is result
    completed.assert_not_called()
