"""P1-SEC-03: 项目日志导出必须受总字节预算约束。

此前仅有 200 run × 200 行的行数上限，单行可达 ~1MiB 时导出体积可接近
40GiB。修复后 ``load_export_task_logs`` 逐行累计 content 的 UTF-8 字节，
预算（``EXPORT_LOG_MAX_TOTAL_BYTES``）耗尽即停止读库，并通过返回值显式
标记截断（路由写入 ``task_logs_truncated`` 顶层字段）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from antcode_web_api.routes.v1 import project_export_logs as export_logs

_TS = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def _entry(content: str, sequence: int = 0):
    return SimpleNamespace(
        log_type="stdout",
        content=content,
        sequence=sequence,
        timestamp=_TS,
        level="INFO",
    )


class _FakeLogService:
    def __init__(self, logs_by_run: dict[str, list]) -> None:
        self._logs_by_run = logs_by_run
        self.list_calls: list[str] = []

    async def list_entries(self, run_id: str, *, limit: int, latest: bool):
        assert latest is True
        self.list_calls.append(run_id)
        return self._logs_by_run.get(run_id, [])[:limit]

    async def count(self, run_id: str) -> int:
        return len(self._logs_by_run.get(run_id, []))


@pytest.fixture
def _patch_log_service(monkeypatch):
    def _install(logs_by_run: dict[str, list]) -> _FakeLogService:
        from antcode_core.application.services.logs import postgres_log_service as module

        fake = _FakeLogService(logs_by_run)
        monkeypatch.setattr(module, "postgres_log_service", fake)
        return fake

    return _install


@pytest.mark.asyncio
async def test_export_logs_within_budget_are_complete(_patch_log_service):
    entries = [_entry("hello", 0), _entry("world", 1)]
    _patch_log_service({"run-1": entries})

    task_logs, truncated = await export_logs.load_export_task_logs(["run-1"])

    assert truncated is False
    assert task_logs["run-1"]["truncated"] is False
    assert [line["content"] for line in task_logs["run-1"]["lines"]] == ["hello", "world"]
    assert task_logs["run-1"]["total_lines"] == len(entries)


@pytest.mark.asyncio
async def test_export_logs_stop_at_total_byte_budget(monkeypatch, _patch_log_service):
    monkeypatch.setattr(export_logs, "EXPORT_LOG_MAX_TOTAL_BYTES", 10)
    entries = [_entry("aaaa", 0), _entry("bbbb", 1), _entry("cccc", 2)]
    _patch_log_service({"run-1": entries})

    task_logs, truncated = await export_logs.load_export_task_logs(["run-1"])

    assert truncated is True
    # 预算 10 字节只装得下前两行（4+4），第三行被裁掉并显式标记
    assert [line["content"] for line in task_logs["run-1"]["lines"]] == ["aaaa", "bbbb"]
    assert task_logs["run-1"]["truncated"] is True
    assert task_logs["run-1"]["total_lines"] == len(entries)


@pytest.mark.asyncio
async def test_export_logs_budget_exhaustion_skips_remaining_runs(monkeypatch, _patch_log_service):
    monkeypatch.setattr(export_logs, "EXPORT_LOG_MAX_TOTAL_BYTES", 8)
    fake = _patch_log_service(
        {
            "run-1": [_entry("12345678", 0)],
            "run-2": [_entry("later", 0)],
        }
    )

    task_logs, truncated = await export_logs.load_export_task_logs(["run-1", "run-2"])

    assert truncated is True
    assert list(task_logs.keys()) == ["run-1"]
    # 预算耗尽后不得继续读库（run-2 整体省略）
    assert fake.list_calls == ["run-1"]


@pytest.mark.asyncio
async def test_export_logs_single_oversized_line_yields_empty_marked_run(monkeypatch, _patch_log_service):
    monkeypatch.setattr(export_logs, "EXPORT_LOG_MAX_TOTAL_BYTES", 4)
    _patch_log_service({"run-1": [_entry("x" * 1024, 0)]})

    task_logs, truncated = await export_logs.load_export_task_logs(["run-1"])

    assert truncated is True
    assert task_logs["run-1"]["lines"] == []
    assert task_logs["run-1"]["truncated"] is True
    assert task_logs["run-1"]["total_lines"] == 1


@pytest.mark.asyncio
async def test_export_logs_budget_counts_utf8_bytes(monkeypatch, _patch_log_service):
    monkeypatch.setattr(export_logs, "EXPORT_LOG_MAX_TOTAL_BYTES", 8)
    # 每个中文字符 3 字节："安特码" = 9 字节 > 8 字节预算
    _patch_log_service({"run-1": [_entry("安特码", 0)]})

    task_logs, truncated = await export_logs.load_export_task_logs(["run-1"])

    assert truncated is True
    assert task_logs["run-1"]["lines"] == []


def test_export_log_budget_constant_is_reasonable():
    """预算必须存在且显著小于旧上限（200 run × 200 行 × 1MiB）。"""
    assert export_logs.EXPORT_LOG_MAX_TOTAL_BYTES == 8 * 1024 * 1024


def test_export_route_uses_budgeted_loader():
    """路由模块必须使用带预算的加载器（防止回退到旧的无预算实现）。"""
    from antcode_web_api.routes.v1 import project as project_routes

    assert project_routes.load_export_task_logs is export_logs.load_export_task_logs
    assert not hasattr(project_routes, "_load_export_task_logs")
