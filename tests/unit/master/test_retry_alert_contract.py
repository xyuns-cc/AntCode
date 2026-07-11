from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.alert import alert_service
from antcode_master.control.retry_loop import RetryService


@pytest.mark.asyncio
async def test_retry_failure_alert_uses_alert_service_contract(monkeypatch):
    send_alert = AsyncMock()
    monkeypatch.setattr(alert_service, "send_alert", send_alert)
    task = SimpleNamespace(name="daily-crawl")
    execution = SimpleNamespace(run_id="run-1", retry_count=3)

    await RetryService()._send_failure_alert(task, execution, "network timeout")

    args, kwargs = send_alert.await_args
    assert "network timeout" in args[0]
    assert kwargs == {
        "level": "ERROR",
        "source": "scheduler",
        "extra": {"title": "任务失败: daily-crawl"},
    }
