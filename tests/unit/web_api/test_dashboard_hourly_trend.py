from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from antcode_core.domain.models.enums import TaskStatus
from antcode_web_api.routes.v1 import dashboard


def test_non_admin_trend_uses_owned_task_subquery(monkeypatch) -> None:
    run_query = MagicMock()
    scoped_query = MagicMock()
    run_query.filter.return_value = scoped_query
    task_query = MagicMock()
    task_ids = MagicMock()
    task_query.values.return_value = task_ids
    task_filter = MagicMock(return_value=task_query)
    run_filter = MagicMock(return_value=run_query)
    monkeypatch.setattr(dashboard.Task, "filter", task_filter)
    monkeypatch.setattr(dashboard.TaskRun, "filter", run_filter)

    result = dashboard._hourly_trend_query(
        SimpleNamespace(is_admin=False, user_id=41),
        datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result is scoped_query
    task_filter.assert_called_once_with(user_id=41)
    assert "created_by" not in run_filter.call_args.kwargs
    assert "task_id__in" in run_query.filter.call_args.kwargs


def test_hourly_trend_buckets_are_chronological_across_midnight() -> None:
    current_hour = datetime(2026, 7, 27, 1, tzinfo=UTC)
    executions = [
        SimpleNamespace(start_time=datetime(2026, 7, 26, 23, 15, tzinfo=UTC), status=TaskStatus.SUCCESS),
        SimpleNamespace(start_time=datetime(2026, 7, 27, 0, 45, tzinfo=UTC), status=TaskStatus.FAILED),
    ]

    buckets = dashboard._hourly_trend_buckets(executions, current_hour)

    assert [bucket["hour"] for bucket in buckets[-3:]] == [23, 0, 1]
    assert buckets[-3] == {"hour": 23, "tasks": 1, "success": 1, "failed": 0}
    assert buckets[-2] == {"hour": 0, "tasks": 1, "success": 0, "failed": 1}
