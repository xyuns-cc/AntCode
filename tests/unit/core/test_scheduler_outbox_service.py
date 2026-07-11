from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.scheduler.outbox_service import (
    SchedulerOutboxService,
)


@pytest.mark.asyncio
async def test_enqueue_uses_supplied_transaction_connection():
    service = SchedulerOutboxService(stream=MagicMock())
    connection = MagicMock()
    created = MagicMock()

    with patch(
        "antcode_core.application.services.scheduler.outbox_service.SchedulerOutbox.create",
        AsyncMock(return_value=created),
    ) as create:
        result = await service.enqueue(
            "task_changed",
            "task",
            42,
            {"task_id": "42"},
            connection=connection,
        )

    assert result is created
    assert create.await_args.kwargs["using_db"] is connection


@pytest.mark.asyncio
async def test_publish_adds_stable_outbox_id_without_maxlen():
    stream = MagicMock()
    stream.xadd = AsyncMock()
    service = SchedulerOutboxService(stream=stream)
    event = MagicMock()
    event.payload = {"task_id": "42"}
    event.event_type = "task_changed"
    event.public_id = "event-id"
    event.created_at = datetime.now(UTC)

    await service._publish(event)

    args = stream.xadd.await_args.args
    assert args[1]["outbox_id"] == "event-id"
    assert stream.xadd.await_args.kwargs == {}
