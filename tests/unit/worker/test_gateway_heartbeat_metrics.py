from datetime import datetime
from types import SimpleNamespace

from antcode_worker.heartbeat.reporter import Heartbeat, Metrics, OSInfo, SpiderStats
from antcode_worker.transport.gateway.codecs import HeartbeatEncoder
from antcode_worker.transport.gateway.heartbeat_metrics import (
    build_metrics_proto,
    heartbeat_metrics_dict,
)

EXPECTED_RUNNING_TASKS = 2
EXPECTED_TASK_COUNT = 11
EXPECTED_PROJECT_COUNT = 3
EXPECTED_ENV_COUNT = 4
EXPECTED_REQUEST_COUNT = 9
EXPECTED_RESPONSE_COUNT = 8
EXPECTED_SUCCESS_RATE = 87.5
EXPECTED_MEMORY_BYTES = 1_048_576
EXPECTED_QUEUED_TASKS = 6
EXPECTED_CPU_CORES = 8


def _heartbeat() -> Heartbeat:
    spider = SpiderStats(
        request_count=9,
        response_count=8,
        item_scraped_count=4,
        error_count=1,
        avg_latency_ms=12.5,
        requests_per_minute=9.0,
        status_codes={"200": 7, "500": 1},
        domain_stats=[
            {
                "domain": "example.com",
                "reqs": 8,
                "successRate": 87.5,
                "latency": 12.5,
            }
        ],
    )
    return Heartbeat(
        worker_id="worker-1",
        status="online",
        metrics=Metrics(
            cpu=10.0,
            memory=20.0,
            disk=30.0,
            running_tasks=2,
            max_concurrent_tasks=8,
            task_count=11,
            project_count=3,
            env_count=4,
            queued_tasks=EXPECTED_QUEUED_TASKS,
            cpu_cores=EXPECTED_CPU_CORES,
            memory_total_bytes=EXPECTED_MEMORY_BYTES,
            memory_used_bytes=EXPECTED_MEMORY_BYTES // 2,
            disk_total_bytes=EXPECTED_MEMORY_BYTES * 10,
            uptime_seconds=30,
            spider_stats=spider,
        ),
        os_info=OSInfo(),
        timestamp=datetime.now(),
    )


def test_metrics_proto_includes_all_worker_and_spider_fields() -> None:
    metrics = build_metrics_proto(heartbeat_metrics_dict(_heartbeat()))

    assert metrics.running_tasks == EXPECTED_RUNNING_TASKS
    assert metrics.task_count == EXPECTED_TASK_COUNT
    assert metrics.project_count == EXPECTED_PROJECT_COUNT
    assert metrics.env_count == EXPECTED_ENV_COUNT
    assert metrics.memory_total_bytes == EXPECTED_MEMORY_BYTES
    assert metrics.queued_tasks == EXPECTED_QUEUED_TASKS
    assert metrics.spider_stats.request_count == EXPECTED_REQUEST_COUNT
    assert dict(metrics.spider_stats.status_codes) == {200: 7, 500: 1}
    assert metrics.spider_stats.domain_stats[0].domain == "example.com"
    assert metrics.spider_stats.domain_stats[0].success_rate == EXPECTED_SUCCESS_RATE


def test_heartbeat_encoder_uses_complete_metrics_contract() -> None:
    request = HeartbeatEncoder.encode_lease(_heartbeat(), "worker-1", "lease-1")

    assert request.current_lease_id == "lease-1"
    assert request.metrics.running_tasks == EXPECTED_RUNNING_TASKS
    assert request.metrics.memory_total_bytes == EXPECTED_MEMORY_BYTES
    assert request.metrics.spider_stats.response_count == EXPECTED_RESPONSE_COUNT


def test_legacy_heartbeat_without_metrics_still_encodes_resources() -> None:
    heartbeat = SimpleNamespace(
        cpu_percent=1.0,
        memory_percent=2.0,
        disk_percent=3.0,
        running_tasks=4,
        max_concurrent_tasks=5,
    )

    values = heartbeat_metrics_dict(heartbeat)

    assert values == {
        "cpu": 1.0,
        "memory": 2.0,
        "disk": 3.0,
        "running_tasks": 4,
        "max_concurrent_tasks": 5,
    }
