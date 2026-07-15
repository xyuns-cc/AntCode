from __future__ import annotations

import asyncio
import stat
import time
from typing import Any

import pytest

from tests.loadtest.tool.api import AntCodeApi, _business_error
from tests.loadtest.tool.binding import target_binding_value, verify_redis_target_binding
from tests.loadtest.tool.config import (
    LoadSettings,
    Stage,
    Thresholds,
    load_tokens,
    parse_stage,
    validate_base_url,
    validate_redis_binding_key,
    validate_redis_url,
)
from tests.loadtest.tool.metrics import LoadReport, OperationSample, assert_report, emit_report, percentile
from tests.loadtest.tool.runner import OperationResult, _execute, run_load
from tests.loadtest.tool.scenarios import create_tasks, created_task_ids, wait_for_successful_runs, worker_list_value
from tests.loadtest.tool.sse import _count_history, _parse_sse_data


async def _sse_messages(messages: list[dict[str, Any]]):
    for message in messages:
        yield message


class _SetupApi:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def create_task(self, _body: dict[str, Any], index: int) -> str:
        if index == 1:
            raise RuntimeError("setup failed")
        return f"task-{index}"

    async def delete_tasks(self, task_ids: list[str]) -> None:
        self.deleted.extend(task_ids)


class _CleanupApi(AntCodeApi):
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def require_json(
        self,
        method: str,
        path: str,
        index: int = 0,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._payload


class _VerifyApi(AntCodeApi):
    def __init__(self, statuses: dict[str, int]) -> None:
        self._statuses = statuses

    async def _task_status(self, task_id: str) -> int:
        return self._statuses[task_id]


class _BindingRedis:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.closed = False

    async def get(self, _key: str) -> str | None:
        return self.value

    async def aclose(self) -> None:
        self.closed = True


class _FailingBindingRedis(_BindingRedis):
    async def get(self, _key: str) -> str | None:
        raise ConnectionError("unreachable")


class _RunApi:
    def __init__(self, runs: dict[str, dict[str, Any]]) -> None:
        self.runs = runs

    async def task_runs(self, task_id: str, _index: int = 0) -> list[dict[str, Any]]:
        return [self.runs[task_id]]


def _settings() -> LoadSettings:
    return LoadSettings(
        base_url="http://127.0.0.1:18000",
        redis_url="redis://127.0.0.1:6379/12",
        redis_binding_key="antcode:loadtest:binding:test",
        tokens=("token",),
        confirmation="FULL",
        stage=Stage(2, 2, 1),
        thresholds=Thresholds(),
        project_id="project-1",
        worker_id="worker-1",
    )


def test_redis_db_zero_is_rejected() -> None:
    with pytest.raises(ValueError, match="DB0"):
        validate_redis_url("redis://localhost:6379/0")


@pytest.mark.parametrize(
    "url",
    ["redis://localhost:6379", "redis://localhost:6379/", "http://localhost:6379/14"],
)
def test_redis_guard_requires_explicit_nonzero_database(url: str) -> None:
    with pytest.raises(ValueError):
        validate_redis_url(url)


def test_redis_binding_key_is_explicit_and_strict() -> None:
    key = "antcode:loadtest:binding:test"
    assert validate_redis_binding_key(key) == key
    with pytest.raises(ValueError):
        validate_redis_binding_key(None)
    with pytest.raises(ValueError):
        validate_redis_binding_key("contains whitespace")


def test_token_file_requires_owner_only_permissions(tmp_path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("first\nsecond\n", encoding="utf-8")
    token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert load_tokens(str(token_file)) == ("first", "second")
    token_file.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    with pytest.raises(ValueError, match="chmod 600"):
        load_tokens(str(token_file))


def test_stage_and_url_validation_are_local() -> None:
    assert parse_stage("4:20:5").request_count == 100
    assert validate_base_url("http://127.0.0.1:18080/") == "http://127.0.0.1:18080"


def test_metrics_compute_and_assert_required_indicators(capsys) -> None:
    stage = parse_stage("1:2:1")
    samples = (
        OperationSample(10.0, 200, "a"),
        OperationSample(30.0, 200, "b"),
    )
    report = LoadReport("self-check", stage, 1.0, samples)
    summary = report.summary
    assert summary.qps == 2.0
    assert summary.p50_ms == percentile((10.0, 30.0), 0.50)
    assert summary.p95_ms == percentile((10.0, 30.0), 0.95)
    assert summary.p99_ms == percentile((10.0, 30.0), 0.99)
    assert summary.status_codes == (("200", 2),)
    assert summary.server_errors_5xx == 0
    assert summary.error_rate == 0.0
    assert_report(report, Thresholds(max_p95_ms=30, max_p99_ms=30), frozenset({200}))
    emit_report(report)
    output = capsys.readouterr().out
    assert 'ANTCODE_LOADTEST_RESULT {"elapsed_seconds": 1.0' in output
    assert '"p99_ms": 29.8' in output


@pytest.mark.asyncio
async def test_business_failure_is_counted_as_failed() -> None:
    assert _business_error({"success": False}) == "api_success_false"

    async def operation(_index: int) -> OperationResult[dict[str, bool]]:
        return OperationResult(200, {"success": False}, "api_success_false")

    report = await run_load("business-failure", Stage(1, 1, 1), operation)
    assert report.summary.failed == 1
    assert report.summary.errors == (("api_success_false", 1),)


@pytest.mark.asyncio
async def test_semaphore_queue_wait_is_included_in_latency() -> None:
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()
    samples = []

    async def operation(_index: int) -> OperationResult[str]:
        return OperationResult(200, "ok")

    submitted_at = time.perf_counter()
    task = asyncio.create_task(_execute(0, semaphore, operation, samples=samples, submitted_at=submitted_at))
    await asyncio.sleep(0.03)
    semaphore.release()
    await task

    assert samples[0][1].latency_ms >= 25


@pytest.mark.asyncio
async def test_redis_binding_requires_exact_target_marker() -> None:
    settings = _settings()
    redis = _BindingRedis(target_binding_value(settings))
    await verify_redis_target_binding(settings, redis_factory=lambda *_args, **_kwargs: redis)
    assert redis.closed is True

    mismatch = _BindingRedis("wrong-target")
    with pytest.raises(RuntimeError, match="binding mismatch"):
        await verify_redis_target_binding(settings, redis_factory=lambda *_args, **_kwargs: mismatch)

    unreachable = _FailingBindingRedis(None)
    with pytest.raises(RuntimeError, match="could not be verified"):
        await verify_redis_target_binding(settings, redis_factory=lambda *_args, **_kwargs: unreachable)
    assert unreachable.closed is True


@pytest.mark.asyncio
async def test_successful_runs_must_execute_on_expected_worker() -> None:
    api = _RunApi(
        {
            "task-1": {"status": "success", "worker_id": "worker-1"},
            "task-2": {"status": "success", "worker_id": "worker-1"},
        }
    )
    elapsed = await wait_for_successful_runs(
        api,  # type: ignore[arg-type]
        ("task-1", "task-2"),
        1,
        expected_worker_id="worker-1",
    )
    assert elapsed >= 0


@pytest.mark.asyncio
async def test_successful_run_on_wrong_worker_is_rejected() -> None:
    api = _RunApi({"task-1": {"status": "success", "worker_id": "worker-2"}})
    with pytest.raises(AssertionError, match="did not execute on Worker"):
        await wait_for_successful_runs(
            api,  # type: ignore[arg-type]
            ("task-1",),
            1,
            expected_worker_id="worker-1",
        )


@pytest.mark.asyncio
async def test_cleanup_rejects_incomplete_success_count() -> None:
    api = _CleanupApi({"data": {"success_count": 0, "failed_count": 0, "failed_ids": []}})
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await api._delete_task_batch(["task-1"])


@pytest.mark.asyncio
async def test_cleanup_rejects_tasks_that_remain_accessible() -> None:
    api = _VerifyApi({"task-1": 404, "task-2": 200})
    with pytest.raises(RuntimeError, match="task-2"):
        await api._verify_tasks_deleted(["task-1", "task-2"])


def test_created_task_ids_include_error_samples_for_cleanup() -> None:
    report = LoadReport(
        "cleanup",
        Stage(1, 1, 1),
        1.0,
        (OperationSample(1.0, 200, {"data": {"id": "task-1"}}, "api_success_false"),),
    )
    assert created_task_ids(report) == ["task-1"]


@pytest.mark.asyncio
async def test_sse_history_counts_log_line_messages() -> None:
    messages = _sse_messages(
        [{"type": "log_line", "data": {"content": "line"}}, {"type": "historical_logs_end", "sent_lines": 1}]
    )
    assert await _count_history(messages) == 1


@pytest.mark.asyncio
async def test_sse_history_rejects_summary_mismatch() -> None:
    messages = _sse_messages([{"type": "historical_logs_end", "sent_lines": 1}])
    with pytest.raises(RuntimeError, match="count mismatch"):
        await _count_history(messages)


@pytest.mark.asyncio
async def test_sse_parser_splits_frames_and_skips_comments() -> None:
    async def lines():
        for line in [
            ": keep-alive",
            "event: log_line",
            'data: {"type": "log_line", "data": {"content": "hello"}}',
            "",
            "event: historical_logs_end",
            'data: {"type": "historical_logs_end", "sent_lines": 1}',
            "",
        ]:
            yield line

    messages = [message async for message in _parse_sse_data(lines())]

    assert messages == [
        {"type": "log_line", "data": {"content": "hello"}},
        {"type": "historical_logs_end", "sent_lines": 1},
    ]


@pytest.mark.asyncio
async def test_partial_task_setup_is_cleaned_up() -> None:
    api = _SetupApi()
    with pytest.raises(ExceptionGroup, match="created tasks were cleaned up"):
        await create_tasks(api, _settings(), 3, prefix="setup")  # type: ignore[arg-type]
    assert api.deleted == ["task-0", "task-2"]


def test_worker_inventory_rejects_missing_public_ids() -> None:
    with pytest.raises(ValueError, match="missing or duplicate"):
        worker_list_value({"data": {"total": 1, "items": [{}]}})
