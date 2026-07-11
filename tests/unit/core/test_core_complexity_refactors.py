from datetime import datetime
from types import SimpleNamespace

import pytest
from antcode_core.application.services.alert.alert_service import AlertService
from antcode_core.application.services.crawl.test_service import CrawlTestService
from antcode_core.application.services.system_config.system_config_service import (
    SystemConfigService,
)
from antcode_core.application.services.workers.worker_heartbeat_service import (
    WorkerHeartbeatService,
)
from antcode_core.application.services.workers.worker_stats_service import WorkerStatsService


def test_alert_config_parsers_preserve_supported_types():
    service = AlertService()

    assert service._parse_config_value("rate_limit_enabled", "yes") is True
    assert service._parse_config_value("rate_limit_window", "30") == 30
    assert service._parse_config_value("retry_delay", "1.5") == 1.5
    assert service._parse_config_value("auto_alert_levels", "ERROR, CRITICAL") == [
        "ERROR",
        "CRITICAL",
    ]
    assert service._parse_config_value("unknown", "value") is None


def test_alert_test_result_summary_counts_exceptions_and_channel_failures():
    results = [
        ("feishu", True, None),
        ("email", False, "发送失败"),
        RuntimeError("network error"),
    ]

    success, failed, errors = AlertService._summarize_test_results(results)

    assert (success, failed) == (1, 2)
    assert errors == ["email: 发送失败", "network error"]


def test_crawl_sample_decoder_handles_bytes_and_url():
    sample = CrawlTestService._decode_sample_entry({b"data": b'{"title":"example"}', b"url": b"https://example.test"})

    assert sample == {"title": "example", "_url": "https://example.test"}


def test_system_config_parser_exposes_parse_error():
    config = SimpleNamespace(config_key="workers", config_value="invalid", value_type="int")

    with pytest.raises(ValueError, match="invalid literal"):
        SystemConfigService._parse_cached_config(config)


def test_worker_heartbeat_metrics_merge_without_mutating_input():
    worker = SimpleNamespace(metrics={"cpu": 10})
    metrics = {"memory": 20}

    heartbeat_metrics = WorkerHeartbeatService._apply_heartbeat_metrics(
        worker,
        metrics,
        {"request_count": 3},
    )

    assert metrics == {"memory": 20}
    assert heartbeat_metrics["spider_stats"] == {"request_count": 3}
    assert worker.metrics == {
        "cpu": 10,
        "memory": 20,
        "spider_stats": {"request_count": 3},
    }


def test_worker_stats_four_hour_buckets_and_zero_metric_summary():
    heartbeats = [
        SimpleNamespace(timestamp=datetime(2026, 7, 10, 5), metrics={"cpu": 20}),
        SimpleNamespace(timestamp=datetime(2026, 7, 10, 7), metrics={"cpu": 40}),
    ]

    buckets = WorkerStatsService._bucket_heartbeats(heartbeats, None, 4)
    timestamps = sorted(buckets)

    assert timestamps == ["2026-07-10 04:00"]
    assert WorkerStatsService._metric_summary(buckets, timestamps, "cpu") == {
        "avg": [30.0],
        "max": [40],
        "min": [20],
    }
    assert WorkerStatsService._metric_summary(buckets, timestamps, "memory") == {
        "avg": [0],
        "max": [0],
        "min": [0],
    }
