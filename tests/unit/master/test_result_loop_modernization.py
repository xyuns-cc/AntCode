"""ResultLoop current Proto behavior."""

import importlib
from unittest.mock import AsyncMock

import pytest
from antcode_contracts import data_pb2
from antcode_master.ingester.result_loop import ResultLoop

result_module = importlib.import_module("antcode_master.ingester.result_loop")


@pytest.mark.asyncio
async def test_handle_message_ignores_status_without_run_id(monkeypatch):
    update_result = AsyncMock()
    monkeypatch.setattr(result_module.task_run_service, "update_result", update_result)

    handled = await ResultLoop()._handle_message(data_pb2.TaskStatus(status=data_pb2.STATUS_COMPLETED))

    assert handled is True
    update_result.assert_not_awaited()
