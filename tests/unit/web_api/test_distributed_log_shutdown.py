import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_shutdown_runs_all_distributed_log_steps_after_follower_failure(monkeypatch) -> None:
    from antcode_core.application.services.workers.distributed_log_service import distributed_log_service
    from antcode_web_api.streams.ingest_follower import ingest_log_follower

    lifespan_module = importlib.import_module("antcode_web_api.lifespan")

    follower_shutdown = AsyncMock(side_effect=RuntimeError("follower failed"))
    detach = MagicMock()
    stop = AsyncMock()
    monkeypatch.setattr(ingest_log_follower, "shutdown", follower_shutdown)
    monkeypatch.setattr(distributed_log_service, "set_notifier", detach)
    monkeypatch.setattr(distributed_log_service, "stop", stop)

    with pytest.raises(ExceptionGroup, match="存在失败步骤"):
        await lifespan_module._shutdown_distributed_log()

    follower_shutdown.assert_awaited_once()
    detach.assert_called_once_with(None)
    stop.assert_awaited_once()
