"""RedisLogStreamService current architecture behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.logs.postgres_log_service as pg_module
import antcode_web_api.websockets.redis_log_stream_service as stream_module
import pytest
from antcode_web_api.websockets.redis_log_stream_service import RedisLogStreamService


@pytest.mark.asyncio
async def test_each_subscriber_receives_its_own_history(monkeypatch):
    service = RedisLogStreamService(namespace="ac")
    entry = SimpleNamespace(
        log_type="stdout",
        content="hello",
        timestamp=None,
        sequence=1,
    )
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(pg_module.postgres_log_service, "list_entries", AsyncMock(return_value=[entry]))
    monkeypatch.setattr(stream_module.websocket_manager, "send_to_connection", send)
    monkeypatch.setattr(service, "_ensure_ingest_task", AsyncMock())

    await service.subscribe("run-1", "connection-1")
    await service.subscribe("run-1", "connection-2")

    connection_ids = [call.args[1] for call in send.await_args_list]
    assert connection_ids.count("connection-1") == 3
    assert connection_ids.count("connection-2") == 3
    assert service._followers["run-1"].ref_count == 2


def test_redis_log_stream_service_has_no_object_storage_history_reader():
    source = stream_module.__loader__.get_source(stream_module.__name__)

    assert "_send_history_from_s3" not in source
    assert "get_log_storage" not in source
    assert "presigned" not in source.lower()
    assert "aiohttp" not in source
