from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.scheduler import task_project_integrity

SCHEDULER_SERVICE = Path("packages/antcode_core/src/antcode_core/application/services/scheduler/scheduler_service.py")
PROJECT_DELETE = Path("packages/antcode_core/src/antcode_core/application/services/projects/project_cascade_delete.py")
# 删除侧的父行锁与在途执行判定住在 project_delete_scope
PROJECT_DELETE_SCOPE = Path(
    "packages/antcode_core/src/antcode_core/application/services/projects/project_delete_scope.py"
)


def _project_query(result):
    query = MagicMock()
    query.using_db.return_value = query
    query.select_for_update.return_value = query
    query.only.return_value = query
    query.first = AsyncMock(return_value=result)
    return query


@pytest.mark.asyncio
async def test_task_creation_locks_parent_project_in_caller_transaction(monkeypatch) -> None:
    project = MagicMock(id=7)
    query = _project_query(project)
    project_filter = MagicMock(return_value=query)
    monkeypatch.setattr(task_project_integrity.Project, "filter", project_filter)
    connection = object()

    result = await task_project_integrity.lock_task_project(connection, 7)

    assert result is project
    project_filter.assert_called_once_with(id=7)
    query.using_db.assert_called_once_with(connection)
    query.select_for_update.assert_called_once_with()


@pytest.mark.asyncio
async def test_task_creation_rejects_deleted_parent_project(monkeypatch) -> None:
    query = _project_query(None)
    monkeypatch.setattr(task_project_integrity.Project, "filter", MagicMock(return_value=query))

    with pytest.raises(ValueError, match="项目不存在或正在删除"):
        await task_project_integrity.lock_task_project(object(), 9)


def test_task_create_and_project_delete_share_parent_row_lock() -> None:
    create_source = SCHEDULER_SERVICE.read_text(encoding="utf-8")
    delete_source = PROJECT_DELETE.read_text(encoding="utf-8")
    scope_source = PROJECT_DELETE_SCOPE.read_text(encoding="utf-8")

    assert create_source.index("lock_task_project(conn, project_id)") < create_source.index("Task.create(")
    assert "Project.filter(id=project_id).using_db(conn).select_for_update()" in scope_source
    # 级联删除必须经由 lock_project_scope 拿到这把锁，不能绕开自己查
    assert "lock_project_scope(conn, project_id)" in delete_source
