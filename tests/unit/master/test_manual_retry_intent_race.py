"""Manual retry and an already-claimed automatic retry must create one run."""

from unittest.mock import AsyncMock

import pytest
from antcode_master.control import retry_intent_guard, retry_loop


@pytest.mark.asyncio
async def test_claimed_automatic_intent_is_acked_after_manual_retry_wins():
    service = retry_loop.RetryService()
    service._process_claimed_intent = AsyncMock(
        side_effect=retry_intent_guard.RetryIntentInvalidError("retry intent 已失效: manual retry consumed source-run")
    )
    service._backend.ack = AsyncMock()
    service._backend.clear_attempts = AsyncMock()
    service._backend.requeue = AsyncMock()
    item = {
        "__raw_payload": "payload",
        "task_id": 1,
        "run_id": "source-run",
    }

    result = await service._handle_claimed_item(item)

    assert result is None
    service._backend.ack.assert_awaited_once_with("payload")
    service._backend.clear_attempts.assert_awaited_once_with("source-run")
    service._backend.requeue.assert_not_awaited()
