from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.scheduler.task_run_counters import (
    FAILURE_STATUSES,
    task_run_outcome_counts,
)
from antcode_core.domain.models.enums import TaskStatus


class _CountQuery:
    def __init__(self, count: int) -> None:
        self.count = AsyncMock(return_value=count)

    def using_db(self, _connection):
        return self


@pytest.mark.asyncio
async def test_task_run_counters_are_derived_from_all_terminal_runs() -> None:
    success_query = _CountQuery(3)
    failure_query = _CountQuery(2)
    connection = MagicMock()

    with patch(
        "antcode_core.application.services.scheduler.task_run_counters.TaskRun.filter",
        MagicMock(side_effect=[success_query, failure_query]),
    ) as task_runs:
        counts = await task_run_outcome_counts(connection, 7)

    assert counts == {"success_count": 3, "failure_count": 2}
    assert task_runs.call_args_list[0].kwargs == {"task_id": 7, "status": TaskStatus.SUCCESS}
    assert task_runs.call_args_list[1].kwargs == {"task_id": 7, "status__in": FAILURE_STATUSES}
