from unittest.mock import AsyncMock

import antcode_web_api.websockets.redis_log_stream_service as stream_module
import pytest
from antcode_web_api.websockets.redis_log_stream_service import RedisLogStreamService


@pytest.mark.asyncio
async def test_history_fails_when_target_connection_is_gone(monkeypatch):
    service = RedisLogStreamService()
    monkeypatch.setattr(
        stream_module.websocket_manager,
        "send_to_connection",
        AsyncMock(return_value=False),
    )

    with pytest.raises(RuntimeError, match="connection-1"):
        await service._send_history("run-1", "connection-1")
