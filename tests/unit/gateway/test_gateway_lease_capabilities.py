from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import common_pb2, control_pb2
from antcode_contracts.wire_contract import WIRE_CONTRACT_CAPABILITY, WORKER_WIRE_CONTRACT_VERSION
from antcode_gateway.services import control_service as control_service_module
from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.lease_heartbeat import decode_lease_heartbeat

EXPECTED_TASK_COUNT = 4
EXPECTED_MEMORY_BYTES = 1_048_576
EXPECTED_QUEUED_TASKS = 6
EXPECTED_CPU_CORES = 8


def _supported_capabilities() -> dict[str, str]:
    """控制面接受的最小能力快照：任务类型 + 当前线协议契约版本。"""
    return {
        "task_types": '["code","rule"]',
        WIRE_CONTRACT_CAPABILITY: str(WORKER_WIRE_CONTRACT_VERSION),
    }


def _request(capabilities: dict[str, str]) -> control_pb2.LeaseRequest:
    return control_pb2.LeaseRequest(
        worker_id="worker-1",
        metrics=common_pb2.Metrics(
            max_concurrent_tasks=8,
            task_count=4,
            project_count=2,
            env_count=3,
            queued_tasks=EXPECTED_QUEUED_TASKS,
            cpu_cores=EXPECTED_CPU_CORES,
            memory_total_bytes=EXPECTED_MEMORY_BYTES,
            spider_stats=common_pb2.SpiderStatsSummary(
                request_count=5,
                status_codes={200: 4, 500: 1},
                domain_stats=[
                    common_pb2.SpiderDomainStats(
                        domain="example.com",
                        request_count=5,
                        success_rate=80.0,
                        avg_latency_ms=15.0,
                    )
                ],
            ),
        ),
        capabilities=capabilities,
    )


def test_lease_capabilities_decode_to_structured_values() -> None:
    payload = decode_lease_heartbeat(
        _request({"task_types": '["code","rule"]', "curl_cffi": '{"enabled":true}'}),
        "worker-1",
    )

    assert payload.capabilities == {
        "task_types": ["code", "rule"],
        "curl_cffi": {"enabled": True},
    }
    assert payload.heartbeat is not None
    assert payload.heartbeat.capabilities == payload.capabilities
    assert payload.heartbeat.task_count == EXPECTED_TASK_COUNT
    assert payload.heartbeat.spider_stats == payload.metrics["spider_stats"]
    assert payload.metrics["task_count"] == EXPECTED_TASK_COUNT
    assert payload.metrics["memory_total_bytes"] == EXPECTED_MEMORY_BYTES
    assert payload.metrics["spider_stats"]["status_codes"] == {"200": 4, "500": 1}
    assert payload.metrics["spider_stats"]["domain_stats"][0] == {
        "domain": "example.com",
        "reqs": 5,
        "successRate": 80.0,
        "latency": 15.0,
    }


def test_lease_capabilities_reject_invalid_json() -> None:
    with pytest.raises(ValueError):
        decode_lease_heartbeat(_request({"task_types": "not-json"}), "worker-1")


@pytest.mark.parametrize(
    "metrics",
    [
        common_pb2.Metrics(max_concurrent_tasks=1, running_tasks=-1),
        common_pb2.Metrics(max_concurrent_tasks=1, cpu=float("nan")),
        common_pb2.Metrics(max_concurrent_tasks=0),
        common_pb2.Metrics(
            max_concurrent_tasks=1,
            spider_stats=common_pb2.SpiderStatsSummary(status_codes={99: 1}),
        ),
        common_pb2.Metrics(
            max_concurrent_tasks=1,
            spider_stats=common_pb2.SpiderStatsSummary(
                domain_stats=[common_pb2.SpiderDomainStats(domain="", request_count=1)],
            ),
        ),
    ],
)
def test_lease_rejects_invalid_external_metrics(metrics) -> None:
    request = control_pb2.LeaseRequest(worker_id="worker-1", metrics=metrics)

    with pytest.raises(ValueError):
        decode_lease_heartbeat(request, "worker-1")


@pytest.mark.asyncio
async def test_lease_grant_is_authoritative_when_legacy_view_fails(monkeypatch) -> None:
    lease = SimpleNamespace(lease_id="lease-1", expires_at_ms=30_000)
    store = MagicMock(
        policy=SimpleNamespace(ttl_ms=30_000, renew_after_ms=10_000),
        grant=AsyncMock(return_value=lease),
    )
    handler = MagicMock(handle=AsyncMock(return_value=False))
    service = GatewayControlService(
        lease_handler=handler,
        lease_store=store,
        lease_authorizer=AsyncMock(return_value=SimpleNamespace(allowed=True)),
    )
    monkeypatch.setattr(
        control_service_module,
        "require_authenticated_worker",
        AsyncMock(return_value="worker-1"),
    )

    response = await service.Lease(_request(_supported_capabilities()), MagicMock())

    assert response.lease_id == "lease-1"
    store.grant.assert_awaited_once_with(
        "worker-1",
        current_lease_id="",
        metrics={
            "cpu": 0.0,
            "memory": 0.0,
            "disk": 0.0,
            "running_tasks": 0,
            "max_concurrent_tasks": 8,
            "task_count": 4,
            "project_count": 2,
            "env_count": 3,
            "queued_tasks": EXPECTED_QUEUED_TASKS,
            "cpu_cores": EXPECTED_CPU_CORES,
            "memory_total_bytes": EXPECTED_MEMORY_BYTES,
            "memory_used_bytes": 0,
            "memory_available_bytes": 0,
            "disk_total_bytes": 0,
            "disk_used_bytes": 0,
            "disk_free_bytes": 0,
            "uptime_seconds": 0,
            "spider_stats": {
                "request_count": 5,
                "response_count": 0,
                "item_scraped_count": 0,
                "error_count": 0,
                "avg_latency_ms": 0.0,
                "requests_per_minute": 0.0,
                "status_codes": {"200": 4, "500": 1},
                "domain_stats": [
                    {
                        "domain": "example.com",
                        "reqs": 5,
                        "successRate": 80.0,
                        "latency": 15.0,
                    }
                ],
            },
        },
        capabilities={"task_types": ["code", "rule"], WIRE_CONTRACT_CAPABILITY: WORKER_WIRE_CONTRACT_VERSION},
    )
    store.revoke.assert_not_called()
