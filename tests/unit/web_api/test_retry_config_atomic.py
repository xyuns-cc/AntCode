from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from antcode_core.domain.models import Task, User
from antcode_web_api.routes.v1 import retry
from antcode_web_api.routes.v1.retry_config import RetryConfigUpdate
from pydantic import ValidationError

_TASK_ID = 11
_OWNER_ID = 7
_UPDATED_RETRY_COUNT = 5
_RETRY_DELAY_SECONDS = 60


def test_retry_config_update_keeps_omitted_fields_out_of_database_changes() -> None:
    config = RetryConfigUpdate(max_retries=0)

    assert config.database_changes() == {"retry_count": 0}


@pytest.mark.parametrize("payload", [{}, {"max_retries": None}, {"retry_delay": None}])
def test_retry_config_update_rejects_empty_or_null_patch(payload) -> None:
    with pytest.raises(ValidationError):
        RetryConfigUpdate.model_validate(payload)


def test_retry_config_update_rejects_unimplemented_strategy() -> None:
    with pytest.raises(ValidationError):
        RetryConfigUpdate(strategy="linear")


@pytest.mark.asyncio
async def test_retry_config_uses_field_update_without_saving_stale_task(monkeypatch) -> None:
    stale_task = SimpleNamespace(
        id=_TASK_ID,
        public_id="task-public",
        name="task",
        user_id=_OWNER_ID,
        retry_count=3,
        retry_delay=_RETRY_DELAY_SECONDS,
        status="stale-status",
        failure_count=1,
        save=AsyncMock(),
    )
    refreshed_task = SimpleNamespace(
        id=_TASK_ID,
        public_id="task-public",
        name="task",
        user_id=_OWNER_ID,
        retry_count=_UPDATED_RETRY_COUNT,
        retry_delay=_RETRY_DELAY_SECONDS,
        status="running",
        failure_count=2,
    )
    get_task = AsyncMock(side_effect=[stale_task, refreshed_task])
    apply_config = AsyncMock(return_value=[])
    monkeypatch.setattr(Task, "get_or_none", get_task)
    monkeypatch.setattr(User, "get_or_none", AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    monkeypatch.setattr(retry, "apply_retry_configuration", apply_config)

    response = await retry.update_retry_config(
        "task-public",
        RetryConfigUpdate(max_retries=_UPDATED_RETRY_COUNT),
        SimpleNamespace(user_id=_OWNER_ID),
    )

    apply_config.assert_awaited_once_with(
        _TASK_ID,
        {"retry_count": _UPDATED_RETRY_COUNT, "updated_at": ANY},
        user_id=_OWNER_ID,
    )
    stale_task.save.assert_not_awaited()
    assert response.data["max_retries"] == _UPDATED_RETRY_COUNT
    assert response.data["retry_delay"] == _RETRY_DELAY_SECONDS
    assert response.data["task_id"] == "task-public"
