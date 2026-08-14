from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.projects.unified_project_service import UnifiedProjectService
from fastapi import HTTPException

BAD_REQUEST = 400
FORBIDDEN = 403
CONFLICT = 409


@pytest.mark.asyncio
async def test_bound_worker_must_resolve_by_public_id() -> None:
    fields = {"bound_worker_id": "404"}
    project = SimpleNamespace(execution_strategy="fixed", bound_worker_id=None, user_id=7)
    worker_query = MagicMock()
    worker_query.using_db.return_value.first = AsyncMock(return_value=None)
    with patch(
        "antcode_core.application.services.projects.unified_project_service.Worker.filter", return_value=worker_query
    ):
        with pytest.raises(HTTPException, match="绑定的 Worker 不存在") as exc:
            await UnifiedProjectService._resolve_execution_binding(fields, project, MagicMock())

    assert exc.value.status_code == BAD_REQUEST


@pytest.mark.asyncio
async def test_bound_worker_requires_use_authorization() -> None:
    worker = SimpleNamespace(id=9)
    fields = {"bound_worker_id": "worker-1"}
    project = SimpleNamespace(execution_strategy="fixed", bound_worker_id=None, user_id=7)
    worker_query = MagicMock()
    worker_query.using_db.return_value.first = AsyncMock(return_value=worker)
    user_query = MagicMock()
    user_query.using_db.return_value.exists = AsyncMock(return_value=False)
    permission_query = MagicMock()
    permission_query.using_db.return_value.exists = AsyncMock(return_value=False)
    with (
        patch(
            "antcode_core.application.services.projects.unified_project_service.Worker.filter",
            return_value=worker_query,
        ),
        patch(
            "antcode_core.application.services.projects.unified_project_service.User.filter", return_value=user_query
        ),
        patch(
            "antcode_core.application.services.projects.unified_project_service.UserWorkerPermission.filter",
            return_value=permission_query,
        ),
    ):
        with pytest.raises(HTTPException, match="无权使用绑定的 Worker") as exc:
            await UnifiedProjectService._resolve_execution_binding(fields, project, MagicMock())

    assert exc.value.status_code == FORBIDDEN


@pytest.mark.asyncio
async def test_switching_to_fixed_requires_a_bound_worker() -> None:
    fields = {"execution_strategy": "fixed"}
    project = SimpleNamespace(execution_strategy="auto", bound_worker_id=None, user_id=7)

    with pytest.raises(HTTPException, match="fixed 策略必须绑定 Worker") as exc:
        await UnifiedProjectService._resolve_execution_binding(fields, project, MagicMock())

    assert exc.value.status_code == BAD_REQUEST


@pytest.mark.asyncio
async def test_switching_to_auto_clears_existing_bound_worker() -> None:
    fields = {"execution_strategy": "auto"}
    project = SimpleNamespace(execution_strategy="fixed", bound_worker_id=9, user_id=7)

    await UnifiedProjectService._resolve_execution_binding(fields, project, MagicMock())

    assert fields["bound_worker_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "model_path"),
    [
        ("_update_rule_config", "ProjectRule"),
        ("_update_file_config", "ProjectFile"),
        ("_update_code_config", "ProjectCode"),
    ],
)
async def test_missing_type_detail_rejects_partial_success(method: str, model_path: str) -> None:
    request = MagicMock()
    getattr(request, f"get_{method.removeprefix('_update_').removesuffix('_config')}_fields").return_value = {"x": 1}
    query = MagicMock()
    query.using_db.return_value.first = AsyncMock(return_value=None)
    service = UnifiedProjectService()
    with patch(
        f"antcode_core.application.services.projects.unified_project_service.{model_path}.filter",
        return_value=query,
    ):
        with pytest.raises(HTTPException, match="项目详细配置不存在") as exc:
            await getattr(service, method)(1, request, MagicMock())

    assert exc.value.status_code == CONFLICT
