"""Guarded HTTP, SSE, and archive log load scenarios."""

from __future__ import annotations

from typing import Any

import pytest

from tests.loadtest.tool.api import AntCodeApi
from tests.loadtest.tool.config import LoadSettings
from tests.loadtest.tool.metrics import assert_report, emit_report
from tests.loadtest.tool.runner import run_load
from tests.loadtest.tool.sse import read_sse_history

pytestmark = [pytest.mark.loadtest_scenario, pytest.mark.asyncio]
HTTP_OK = frozenset({200})
SSE_OK = frozenset({200})


@pytest.mark.asyncio
async def test_high_volume_log_streaming(load_settings: LoadSettings) -> None:
    """Open ticket-authenticated SSE streams and consume complete log history."""
    run_ids = _require_run_ids(load_settings)
    async with AntCodeApi(load_settings) as api:

        async def stream(index: int):
            run_id = run_ids[index % len(run_ids)]
            return await read_sse_history(api, load_settings, run_id, index=index)

        report = await run_load("sse-log-history", load_settings.stage, stream)
    assert_report(report, load_settings.thresholds, SSE_OK)
    assert len(report.values) == report.summary.successful
    assert all(lines >= load_settings.min_log_lines for lines in report.values)
    emit_report(report)


@pytest.mark.asyncio
async def test_many_concurrent_log_readers(load_settings: LoadSettings) -> None:
    """Read retained raw logs with concurrent authenticated HTTP clients."""
    run_ids = _require_run_ids(load_settings)
    async with AntCodeApi(load_settings) as api:

        async def read(index: int):
            run_id = run_ids[index % len(run_ids)]
            return await api.call("GET", f"/logs/runs/{run_id}?format=raw&lines=10000", index)

        report = await run_load("http-log-readers", load_settings.stage, read)
    assert_report(report, load_settings.thresholds, HTTP_OK)
    line_counts = tuple(_line_count(value) for value in report.values)
    assert all(count >= load_settings.min_log_lines for count in line_counts)
    emit_report(report)


@pytest.mark.asyncio
async def test_log_archival_throughput(load_settings: LoadSettings) -> None:
    """Download JSON log archives and require non-empty structured payloads."""
    run_ids = _require_run_ids(load_settings)
    async with AntCodeApi(load_settings) as api:

        async def download(index: int):
            run_id = run_ids[index % len(run_ids)]
            return await api.call("GET", f"/tasks/runs/{run_id}/logs/download?format=json", index)

        report = await run_load("log-archive-download", load_settings.stage, download)
    assert_report(report, load_settings.thresholds, HTTP_OK)
    assert len(report.values) == report.summary.successful
    assert all(_archive_has_logs(value) for value in report.values)
    emit_report(report)


def _require_run_ids(settings: LoadSettings) -> tuple[str, ...]:
    if not settings.run_ids:
        raise ValueError("log scenarios require ANTCODE_LOADTEST_RUN_IDS")
    return settings.run_ids


def _line_count(value: Any) -> int:
    data = value.get("data") if isinstance(value, dict) else None
    count = data.get("lines_count") if isinstance(data, dict) else None
    if not isinstance(count, int):
        raise ValueError("raw log response has no lines_count")
    return count


def _archive_has_logs(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    output = value.get("output")
    error = value.get("error")
    return bool(output or error)
