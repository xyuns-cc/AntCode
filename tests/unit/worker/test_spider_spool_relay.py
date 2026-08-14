"""SpiderData 本地 spool 与 Worker 主进程 relay 测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_scrapy.sinks.spool_sink import SpoolSpiderDataSink
from antcode_worker.domain.enums import RunStatus
from antcode_worker.domain.models import ExecPlan, ExecResult, RunContext, RuntimeHandle
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.spider_spool import relay_spider_spool
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.transport.base import TaskMessage


def _record(record_type: str, sequence: int = 0) -> str:
    payload = {
        "run_id": "run-1",
        "project_id": "project-1",
        "spider_name": "rule",
    }
    if record_type == "item":
        payload.update(
            {
                "item_id": f"item-{sequence}",
                "item_type": "default",
                "data": json.dumps({"sequence": sequence}),
                "url": "https://example.com",
                "timestamp": "2026-07-13T00:00:00",
                "sequence": sequence,
            }
        )
    else:
        payload.update({"status": "running", "items_count": str(sequence)})
    return json.dumps({"type": record_type, "payload": payload})


def _write_spool(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


@pytest.mark.asyncio
async def test_spool_sink_writes_only_local_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    path.touch(mode=0o600)
    sink = SpoolSpiderDataSink(str(path))
    await sink.open(run_id="run-1", project_id="project-1", spider_name="rule", namespace="ignored")
    await sink.write_item(
        item_id="item-1",
        item_type="default",
        data_json='{"title":"ok"}',
        url="https://example.com",
        timestamp="2026-07-13T00:00:00",
        sequence=1,
    )
    await sink.close({"status": "completed", "items_count": "1"})

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["item", "meta"]
    assert records[0]["payload"]["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_relay_uses_bounded_batches_and_preserves_meta_order(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    lines = [_record("meta"), *(_record("item", i) for i in range(1, 122)), _record("meta", 121)]
    _write_spool(path, "\n".join(lines) + "\n")
    events: list[tuple[str, int]] = []
    transport = MagicMock()

    async def report(_run_id, items):
        events.append(("items", len(items)))
        return True

    async def update(_run_id, meta):
        events.append(("meta", int(meta["items_count"])))
        return True

    transport.report_spider_data = report
    transport.update_spider_meta = update

    await relay_spider_spool(
        str(path),
        transport,
        expected_run_id="run-1",
        expected_project_id="project-1",
    )

    assert events == [("meta", 0), ("items", 50), ("items", 50), ("items", 21), ("meta", 121)]


@pytest.mark.asyncio
async def test_relay_failure_is_exposed(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    _write_spool(path, _record("item", 1) + "\n")
    transport = MagicMock()
    transport.report_spider_data = AsyncMock(return_value=False)
    transport.update_spider_meta = AsyncMock(return_value=True)

    with pytest.raises(RuntimeError, match="batch relay 失败"):
        await relay_spider_spool(
            str(path),
            transport,
            expected_run_id="run-1",
            expected_project_id="project-1",
        )


@pytest.mark.asyncio
async def test_spool_sink_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.touch(mode=0o600)
    path = tmp_path / "spool.jsonl"
    path.symlink_to(target)
    sink = SpoolSpiderDataSink(str(path))

    with pytest.raises(OSError):
        await sink.open(run_id="run-1", project_id="project-1", spider_name="rule", namespace="ignored")


@pytest.mark.asyncio
async def test_relay_rejects_wide_permissions(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    _write_spool(path, _record("item", 1) + "\n")
    path.chmod(0o644)

    with pytest.raises(PermissionError, match="0600"):
        await relay_spider_spool(
            str(path),
            MagicMock(),
            expected_run_id="run-1",
            expected_project_id="project-1",
        )


@pytest.mark.asyncio
async def test_relay_rejects_boolean_sequence(tmp_path: Path) -> None:
    raw = json.loads(_record("item", 1))
    raw["payload"]["sequence"] = True
    path = tmp_path / "spool.jsonl"
    _write_spool(path, json.dumps(raw) + "\n")

    with pytest.raises(RuntimeError, match="非负整数"):
        await relay_spider_spool(
            str(path),
            MagicMock(),
            expected_run_id="run-1",
            expected_project_id="project-1",
        )


def _engine_fixture(path: Path, status: RunStatus, relay_ok: bool) -> tuple[Engine, RunContext, TaskMessage]:
    transport = MagicMock()
    transport.report_result = AsyncMock(return_value=True)
    transport.report_spider_data = AsyncMock(return_value=relay_ok)
    transport.update_spider_meta = AsyncMock(return_value=True)
    transport._lease_id = "lease-1"
    executor = MagicMock()
    executor.run = AsyncMock(return_value=ExecResult(run_id="run-1", status=status))
    executor.config = ExecutorConfig()
    registry = MagicMock()
    registry.build_plan = AsyncMock(
        return_value=ExecPlan(
            command="python",
            plugin_name="rule",
            env={"ANTCODE_SPIDER_SPOOL_PATH": str(path)},
        )
    )
    engine = Engine(transport=transport, executor=executor, plugin_registry=registry)
    engine._prepare_runtime = AsyncMock(
        return_value=RuntimeHandle(path="/runtime", runtime_hash="hash", python_executable="python")
    )
    context = RunContext(run_id="run-1", task_id="task-1", project_id="project-1")
    task = TaskMessage(
        task_id="task-1",
        project_id="project-1",
        project_type="rule",
        params={"target_url": "https://example.com", "extraction_rules": [{"field": "title"}]},
        run_id="run-1",
    )
    return engine, context, task


@pytest.mark.asyncio
async def test_cancelled_run_relays_partial_spool(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    _write_spool(path, _record("item", 1) + "\n")
    engine, context, task = _engine_fixture(path, RunStatus.CANCELLED, True)
    await engine.state_manager.add("run-1", "task-1")

    result = await engine._execute_task(context, task)

    assert result.status == RunStatus.CANCELLED
    engine._transport.report_spider_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_relay_failure_overrides_cancelled_result(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    _write_spool(path, _record("item", 1) + "\n")
    engine, context, task = _engine_fixture(path, RunStatus.CANCELLED, False)
    await engine.state_manager.add("run-1", "task-1")

    result = await engine._execute_task(context, task)

    assert result.status == RunStatus.FAILED
    assert "batch relay 失败" in (result.error_message or "")


@pytest.mark.asyncio
async def test_forced_cancel_relays_partial_spool_and_records_failure(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    _write_spool(path, _record("item", 1) + "\n")
    engine, context, task = _engine_fixture(path, RunStatus.CANCELLED, False)
    engine._executor.run = AsyncMock(side_effect=asyncio.CancelledError)
    await engine.state_manager.add("run-1", "task-1")

    with pytest.raises(asyncio.CancelledError):
        await engine._execute_task(context, task)

    engine._transport.report_spider_data.assert_awaited_once()
    assert "batch relay 失败" in engine._forced_cancel_relay_errors["run-1"]
    result = engine._build_forced_cancel_result(
        "run-1",
        engine._forced_cancel_relay_errors["run-1"],
    )
    assert result.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_forced_cancel_relay_interrupted_by_second_cancel_stops_spool_reader(tmp_path: Path) -> None:
    """W1 回归：shield 期间二次取消（如 event loop 关停）必须先中断 relay。

    退化行为是 CancelledError 绕过 except Exception，shield 把 relay 任务
    留在后台继续读 spool，与上层 finally 的 _cleanup_rule_tmp rmtree 竞态，
    造成 SpiderData 静默丢失。不变量：_relay_on_forced_cancel 返回前
    relay 必须已停止读 spool，且中断被记入 _forced_cancel_relay_errors。
    """
    path = tmp_path / "spool.jsonl"
    _write_spool(path, _record("item", 1) + "\n")
    relay_started = asyncio.Event()
    relay_cancelled = asyncio.Event()
    transport = MagicMock()

    async def blocking_report(_run_id, _items):
        relay_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            relay_cancelled.set()
            raise
        return True

    transport.report_spider_data = blocking_report
    transport.update_spider_meta = AsyncMock(return_value=True)
    engine = Engine(transport=transport, executor=MagicMock())
    plan = ExecPlan(
        command="python",
        plugin_name="rule",
        env={"ANTCODE_SPIDER_SPOOL_PATH": str(path)},
    )
    context = RunContext(run_id="run-1", task_id="task-1", project_id="project-1")

    outer = asyncio.create_task(engine._relay_on_forced_cancel(plan, context))
    await relay_started.wait()
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    assert relay_cancelled.is_set()
    assert "中断" in engine._forced_cancel_relay_errors["run-1"]


@pytest.mark.asyncio
async def test_runtime_env_cannot_override_spool_path() -> None:
    context = RunContext(
        run_id="run-1",
        task_id="task-1",
        project_id="project-1",
        runtime_spec=SimpleNamespace(env_vars={"ANTCODE_SPIDER_SPOOL_PATH": "/tmp/evil"}),
    )
    plan = ExecPlan(command="python", plugin_name="rule")

    with pytest.raises(RuntimeError, match="不得覆盖"):
        Engine._apply_runtime_env(plan, context)
