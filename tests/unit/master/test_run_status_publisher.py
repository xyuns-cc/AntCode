"""Persisted run-status realtime publication tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_master.ingester.run_status_publisher as publisher_module
import pytest


class _TaskRunQuery:
    def __init__(self, execution):
        self._execution = execution

    def only(self, *_fields):
        return self

    async def first(self):
        return self._execution


@pytest.mark.asyncio
async def test_publish_uses_final_persisted_status(monkeypatch):
    execution = SimpleNamespace(
        status=SimpleNamespace(value="failed"),
        error_message="boom",
    )
    task_run = SimpleNamespace(filter=lambda **_filters: _TaskRunQuery(execution))
    publish = AsyncMock()
    monkeypatch.setattr(publisher_module, "TaskRun", task_run)
    monkeypatch.setattr(publisher_module, "publish_sse_event", publish)

    await publisher_module.publish_persisted_run_status("run-1")

    message = publish.await_args.args[0]
    assert message["type"] == "run_status"
    assert message["run_id"] == "run-1"
    assert message["data"] == {
        "status": "failed",
        "progress": 100.0,
        "message": "任务执行失败: boom",
    }


@pytest.mark.asyncio
async def test_publish_rejects_missing_persisted_run(monkeypatch):
    task_run = SimpleNamespace(filter=lambda **_filters: _TaskRunQuery(None))
    monkeypatch.setattr(publisher_module, "TaskRun", task_run)

    with pytest.raises(RuntimeError, match="结果更新后执行记录消失"):
        await publisher_module.publish_persisted_run_status("run-missing")
