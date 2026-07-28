from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_worker.app.lifecycle import Lifecycle


class _OfflineTransport:
    is_connected = False

    def on_state_change(self, _callback):
        return None

    async def start(self):
        return False

    async def stop(self, grace_period=0):
        return None


@pytest.mark.asyncio
async def test_worker_startup_fails_when_transport_cannot_start():
    lifecycle = Lifecycle()
    runtime_manager = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    container = SimpleNamespace(
        transport=_OfflineTransport(),
        runtime_manager=runtime_manager,
        executor=None,
        observability_server=None,
        heartbeat_reporter=SimpleNamespace(),
        engine=None,
    )

    with pytest.raises(RuntimeError, match="传输层启动失败"):
        await lifecycle.startup(container)

    runtime_manager.start.assert_not_awaited()
