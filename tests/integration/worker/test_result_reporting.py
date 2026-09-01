"""Direct/Gateway result reporting integration tests."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_worker.transport.base import ServerConfig, TaskResult
from antcode_worker.transport.redis.keys import RedisKeys

REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL", "")
pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="ANTCODE_INTEGRATION_REDIS_URL is required for worker integration tests",
)
RESULT_STREAM = RedisKeys().task_result_stream()
REPORT_COUNT = 3
CONCURRENT_REPORT_COUNT = 10
LOAD_REPORT_COUNT = 50


@dataclass(frozen=True)
class ReportingCase:
    task_id: str
    worker_id: str
    transport: Any
    redis_client: Any


@pytest.fixture
def unique_task_id() -> str:
    return f"result-task-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_stream_prefix() -> str:
    return f"test:result:{uuid.uuid4().hex[:8]}:"


@pytest.fixture
def unique_worker_id() -> str:
    return f"result-worker-{uuid.uuid4().hex[:8]}"


def _make_result(
    task_id: str,
    *,
    status: str = "success",
    exit_code: int = 0,
    error_message: str = "",
    duration_ms: float = 100.0,
) -> TaskResult:
    now = datetime.now()
    return TaskResult(
        run_id=f"run-{task_id}",
        task_id=task_id,
        status=status,
        exit_code=exit_code,
        error_message=error_message,
        started_at=now,
        finished_at=now,
        duration_ms=duration_ms,
    )


async def _read_statuses(redis_client: Any, *, count: int = 200) -> list[data_pb2.TaskStatus]:
    entries = await redis_client.xrevrange(RESULT_STREAM, "+", "-", count=count)
    return [data_pb2.TaskStatus.FromString(fields[PROTO_FIELD]) for _, fields in entries]


def _status_for(statuses: list[data_pb2.TaskStatus], task_id: str) -> data_pb2.TaskStatus:
    matches = [status for status in statuses if status.task_id == task_id]
    assert len(matches) == 1, f"expected one result for {task_id}, found {len(matches)}"
    return matches[0]


@asynccontextmanager
async def _reporting_context(
    direct_transport_factory: Any,
    worker_id: str,
    stream_prefix: str,
) -> AsyncIterator[tuple[Any, Any]]:
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)
    config = ServerConfig(redis_url=REDIS_URL, task_stream_prefix=stream_prefix)
    transport = direct_transport_factory(REDIS_URL, worker_id, config)
    await transport.start()
    lease_id, _, _, revoked = await transport.lease_renew("")
    assert lease_id and not revoked
    try:
        yield transport, redis_client
    finally:
        await transport.deregister("integration-test-cleanup")
        await transport.stop()
        await redis_client.aclose()


@pytest_asyncio.fixture
async def reporting_case(request: pytest.FixtureRequest) -> AsyncIterator[ReportingCase]:
    factory = request.getfixturevalue("direct_transport_factory")
    task_id = request.getfixturevalue("unique_task_id")
    worker_id = request.getfixturevalue("unique_worker_id")
    stream_prefix = request.getfixturevalue("unique_stream_prefix")
    async with _reporting_context(factory, worker_id, stream_prefix) as (transport, redis_client):
        yield ReportingCase(task_id, worker_id, transport, redis_client)


@pytest.mark.integration
class TestResultReporting:
    @pytest.mark.asyncio
    async def test_single_result_report(self, reporting_case: ReportingCase) -> None:
        case = reporting_case
        assert await case.transport.report_result(_make_result(case.task_id))
        status = _status_for(await _read_statuses(case.redis_client), case.task_id)
        assert status.run_id == f"run-{case.task_id}"
        assert status.worker_id == case.worker_id
        assert status.status == data_pb2.Status.STATUS_COMPLETED
        assert status.exit_code == 0

    @pytest.mark.asyncio
    async def test_repeated_result_report_has_at_least_once_semantics(
        self,
        reporting_case: ReportingCase,
    ) -> None:
        case = reporting_case
        result = _make_result(case.task_id, duration_ms=2000.0)
        reports = [await case.transport.report_result(result) for _ in range(REPORT_COUNT)]
        assert all(reports)
        statuses = await _read_statuses(case.redis_client)
        matching = [status for status in statuses if status.task_id == case.task_id]
        assert len(matching) == REPORT_COUNT
        assert all(status.run_id == result.run_id for status in matching)

    @pytest.mark.asyncio
    async def test_result_report_with_error(self, reporting_case: ReportingCase) -> None:
        case = reporting_case
        result = _make_result(
            case.task_id,
            status="failed",
            exit_code=1,
            error_message="Task execution failed: ImportError",
        )
        assert await case.transport.report_result(result)
        status = _status_for(await _read_statuses(case.redis_client), case.task_id)
        assert status.status == data_pb2.Status.STATUS_FAILED
        assert status.exit_code == 1
        assert "ImportError" in status.error_message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "terminal_case",
        [
            ("timeout", -1, data_pb2.Status.STATUS_TIMEOUT),
            ("cancelled", -2, data_pb2.Status.STATUS_CANCELLED),
        ],
    )
    async def test_terminal_result_statuses(
        self,
        terminal_case: tuple[str, int, int],
        reporting_case: ReportingCase,
    ) -> None:
        result_status, exit_code, expected_status = terminal_case
        case = reporting_case
        result = _make_result(case.task_id, status=result_status, exit_code=exit_code)
        assert await case.transport.report_result(result)
        status = _status_for(await _read_statuses(case.redis_client), case.task_id)
        assert status.status == expected_status
        assert status.exit_code == exit_code


@pytest.mark.integration
class TestGatewayResultIdempotency:
    def test_gateway_result_cache(self, unique_task_id: str) -> None:
        from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport

        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id=f"gateway-worker-{uuid.uuid4().hex[:8]}",
            enable_receipt_idempotency=True,
            receipt_cache_ttl=60.0,
        )
        transport = GatewayTransport(gateway_config=config)
        cache_key = f"result:{unique_task_id}"
        transport._cache_result(cache_key, True)
        assert transport._get_cached_result(cache_key) is True
        assert transport._get_cached_result("non-existent-key") is None

    def test_gateway_ack_cache(self, unique_task_id: str) -> None:
        from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport

        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id=f"gateway-worker-{uuid.uuid4().hex[:8]}",
            enable_receipt_idempotency=True,
        )
        transport = GatewayTransport(gateway_config=config)
        cache_key = f"ack:{unique_task_id}"
        transport._cache_result(cache_key, True)
        assert transport._get_cached_result(cache_key) is True

    def test_receipt_tracking_idempotency(self) -> None:
        from antcode_worker.transport.gateway.reconnect import ReconnectConfig, ReconnectManager

        manager = ReconnectManager(
            ReconnectConfig(
                enable_receipt_tracking=True,
                receipt_cache_size=100,
                receipt_ttl=60.0,
            )
        )
        receipt_id = f"receipt-{uuid.uuid4().hex[:8]}"
        assert manager.track_receipt(receipt_id, "report_result") is True
        assert manager.track_receipt(receipt_id, "report_result") is False
        manager.complete_receipt(receipt_id, success=True)
        assert manager.is_receipt_completed(receipt_id) is True
        assert manager.track_receipt(receipt_id, "report_result") is False


@pytest.mark.integration
class TestResultReportingConcurrency:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("report_count", [CONCURRENT_REPORT_COUNT, LOAD_REPORT_COUNT])
    async def test_concurrent_result_reports(
        self,
        report_count: int,
        reporting_case: ReportingCase,
    ) -> None:
        case = reporting_case
        task_ids = [f"concurrent-{index}-{uuid.uuid4().hex[:6]}" for index in range(report_count)]
        reports = await asyncio.gather(*(case.transport.report_result(_make_result(task_id)) for task_id in task_ids))
        assert all(reports)
        statuses = await _read_statuses(case.redis_client, count=LOAD_REPORT_COUNT * 4)
        stored_task_ids = {status.task_id for status in statuses}
        assert not set(task_ids) - stored_task_ids
