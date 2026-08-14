from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import log_queries
from fastapi import HTTPException, status

from tests.unit.web_api.log_pagination_test_support import FakeLogQuery, stored_row
from tests.unit.web_api.test_logs_runtime_behavior import logs_route

TASK_LOG_PAGE = 3
TASK_LOG_PAGE_SIZE = 10
TASK_LOG_TOTAL_REMAINDER = 11


@pytest.mark.asyncio
async def test_structured_run_logs_page_stored_entries_without_full_log_read(monkeypatch):
    query = FakeLogQuery([stored_row(3), stored_row(4)], total=7)
    execution = SimpleNamespace(task_id="task-1")
    monkeypatch.setattr(logs_route.TaskLog, "filter", lambda **_kwargs: query)
    monkeypatch.setattr(
        logs_route.log_security_service,
        "verify_log_access_permission",
        AsyncMock(return_value=execution),
    )
    full_read = AsyncMock(side_effect=AssertionError("不得物化完整日志"))
    monkeypatch.setattr(logs_route.task_log_service, "get_execution_logs", full_read)

    response = await logs_route.get_run_logs(
        "run-1",
        format="structured",
        log_type=None,
        level=None,
        search=None,
        page=2,
        size=2,
        current_user=SimpleNamespace(user_id=1),
    )

    assert response.data.structured_data.total == query.total
    assert [item.id for item in response.data.structured_data.items] == [3, 4]
    assert query.offset_value == (response.data.structured_data.page - 1) * response.data.structured_data.size
    assert query.limit_value == response.data.structured_data.size
    full_read.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_logs_page_all_run_logs_with_log_entry_total(monkeypatch):
    page = TASK_LOG_PAGE
    size = TASK_LOG_PAGE_SIZE
    query = FakeLogQuery([stored_row(9)], total=size * page + TASK_LOG_TOTAL_REMAINDER)
    task = SimpleNamespace(id=7, public_id="task-public")
    run_query = SimpleNamespace(values=lambda *_fields: "run-id-query")
    monkeypatch.setattr(logs_route.QueryHelper, "get_by_id_or_public_id", AsyncMock(return_value=task))
    monkeypatch.setattr(logs_route.TaskRun, "filter", lambda **_kwargs: run_query)
    monkeypatch.setattr(logs_route, "Subquery", lambda value: ("subquery", value))
    monkeypatch.setattr(logs_route.TaskLog, "filter", lambda **_kwargs: query)

    response = await logs_route.get_task_logs(
        "task-public",
        page=page,
        size=size,
        log_type=None,
        level=None,
        start_time=None,
        end_time=None,
        search=None,
        current_user=SimpleNamespace(user_id=1),
    )

    assert response.data.total == query.total
    assert response.data.page == page
    assert response.data.size == size
    assert response.data.items[0].task_id == "task-public"
    assert query.offset_value == (response.data.page - 1) * response.data.size
    assert query.limit_value == response.data.size


@pytest.mark.asyncio
async def test_log_page_rejects_response_over_byte_budget(monkeypatch):
    query = FakeLogQuery([stored_row(1, content="x" * 200)], total=1)
    monkeypatch.setattr(log_queries, "MAX_LOG_PAGE_BYTES", 100)

    with pytest.raises(HTTPException) as exc_info:
        await log_queries.paginate_log_query(query, page=1, size=1, task_id="task-1")

    assert exc_info.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE


@pytest.mark.asyncio
async def test_stored_legacy_stderr_info_is_exposed_as_error():
    query = FakeLogQuery([stored_row(1, level="INFO", log_type="stderr")], total=1)

    result = await log_queries.paginate_log_query(query, page=1, size=1, task_id="task-1")

    assert result.items[0].level == log_queries.LogLevel.ERROR


@pytest.mark.asyncio
async def test_stored_legacy_uppercase_stderr_is_exposed_as_error():
    query = FakeLogQuery([stored_row(1, level="INFO", log_type="STDERR")], total=1)

    result = await log_queries.paginate_log_query(query, page=1, size=1, task_id="task-1")

    assert result.items[0].level == log_queries.LogLevel.ERROR


class _Predicate:
    def __init__(self, **kwargs):
        self.value = ("q", kwargs)

    @classmethod
    def from_value(cls, value):
        predicate = cls()
        predicate.value = value
        return predicate

    def __or__(self, other):
        return self.from_value(("or", self.value, other.value))

    def __and__(self, other):
        return self.from_value(("and", self.value, other.value))

    def __invert__(self):
        return self.from_value(("not", self.value))


@pytest.mark.parametrize(
    "level, expected",
    [
        ("ERROR", ("or", ("q", {"log_type__iexact": "stderr"}), ("q", {"level": "ERROR"}))),
        ("INFO", ("and", ("not", ("q", {"log_type__iexact": "stderr"})), ("q", {"level": "INFO"}))),
    ],
)
def test_level_filter_uses_api_visible_stderr_semantics(monkeypatch, level, expected):
    query = FakeLogQuery([], total=0)
    monkeypatch.setattr(log_queries, "Q", _Predicate)

    log_queries.filter_log_query(
        query,
        log_type=None,
        level=log_queries.LogLevel(level),
        search=None,
    )

    assert query.expressions[0].value == expected


@pytest.mark.asyncio
async def test_error_filter_includes_legacy_stderr_info(monkeypatch):
    query = FakeLogQuery([stored_row(1, level="INFO", log_type="stderr")], total=1)
    execution = SimpleNamespace(task_id="task-1")
    monkeypatch.setattr(logs_route.TaskLog, "filter", lambda **_kwargs: query)
    monkeypatch.setattr(
        logs_route.log_security_service,
        "verify_log_access_permission",
        AsyncMock(return_value=execution),
    )

    response = await logs_route.get_run_logs(
        "run-1",
        format="structured",
        log_type=None,
        level="ERROR",
        lines=None,
        search=None,
        page=1,
        size=32,
        current_user=SimpleNamespace(user_id=1),
    )

    assert response.data.structured_data.items[0].level == log_queries.LogLevel.ERROR
    assert query.expressions


@pytest.mark.asyncio
async def test_structured_run_logs_rejects_legacy_lines_parameter(monkeypatch):
    permission = AsyncMock()
    monkeypatch.setattr(logs_route.log_security_service, "verify_log_access_permission", permission)

    with pytest.raises(HTTPException) as exc_info:
        await logs_route.get_run_logs(
            "run-1",
            format="structured",
            lines=100,
            current_user=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "page 和 size" in exc_info.value.detail
    permission.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        ("raw", "invalid", None, "无效的日志类型"),
        ("structured", None, "invalid", "无效的日志级别"),
    ],
)
async def test_get_run_logs_preserves_invalid_filter_errors(monkeypatch, case):
    format_value, log_type, level, expected_detail = case
    execution = SimpleNamespace(task_id="task-1")
    monkeypatch.setattr(
        logs_route.log_security_service,
        "verify_log_access_permission",
        AsyncMock(return_value=execution),
    )

    with pytest.raises(HTTPException) as exc_info:
        await logs_route.get_run_logs(
            run_id="run-1",
            format=format_value,
            log_type=log_type,
            level=level,
            lines=None,
            search=None,
            current_user=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == expected_detail


@pytest.mark.asyncio
async def test_get_task_logs_maps_missing_or_inaccessible_task_to_404(monkeypatch):
    monkeypatch.setattr(
        logs_route.QueryHelper,
        "get_by_id_or_public_id",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await logs_route.get_task_logs(
            task_id="task-missing",
            current_user=SimpleNamespace(user_id=1),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "任务不存在或无权访问"
