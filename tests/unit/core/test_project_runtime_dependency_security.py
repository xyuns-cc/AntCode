from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.projects.project_service import ProjectService
from antcode_core.application.services.runtime import runtime_control_service
from antcode_core.domain.models.enums import RuntimeKind, RuntimeScope
from fastapi import HTTPException, status


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        public_id="project-security-test",
        name="security-test",
        save=AsyncMock(),
    )


def _request(dependencies: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        worker_id="worker-security-test",
        runtime_scope=RuntimeScope.PRIVATE,
        runtime_kind=RuntimeKind.PYTHON,
        use_existing_env=False,
        existing_env_name=None,
        env_name="private-security-test",
        python_version="3.11",
        shared_runtime_key=None,
        dependencies=dependencies,
    )


def _existing_request() -> SimpleNamespace:
    request = _request([])
    request.use_existing_env = True
    request.existing_env_name = "private-existing"
    request.env_name = None
    request.python_version = None
    return request


@pytest.mark.asyncio
async def test_worker_use_user_cannot_install_dependencies_during_project_creation() -> None:
    service = ProjectService()
    worker = SimpleNamespace(id=7, name="worker-security-test")

    with (
        patch.object(
            service,
            "_authorize_worker",
            AsyncMock(return_value=(worker, "normal-user", False)),
        ),
        patch.object(runtime_control_service, "create_env", AsyncMock()) as create_env,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service._setup_worker_environment(
                _project(),
                _request(["requests==2.32.0"]),
                user_id=10,
                conn=object(),
            )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    create_env.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_admin", "dependencies"),
    [
        (True, ["requests==2.32.0"]),
        (False, []),
    ],
)
async def test_authorized_project_environment_creation_keeps_expected_packages(
    is_admin: bool,
    dependencies: list[str],
) -> None:
    service = ProjectService()
    project = _project()
    worker = SimpleNamespace(id=7, name="worker-security-test")
    create_result = {"success": True, "data": {"name": "private-security-test"}}

    with (
        patch.object(
            service,
            "_authorize_worker",
            AsyncMock(return_value=(worker, "creator", is_admin)),
        ),
        patch.object(
            runtime_control_service,
            "create_env",
            AsyncMock(return_value=create_result),
        ) as create_env,
    ):
        await service._setup_worker_environment(
            project,
            _request(dependencies),
            user_id=10,
            conn=object(),
        )

    assert create_env.await_args.kwargs["packages"] == dependencies
    project.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_cannot_bind_another_users_private_runtime() -> None:
    service = ProjectService()
    worker = SimpleNamespace(id=7, name="worker-security-test")
    env = {
        "success": True,
        "data": {
            "name": "private-existing",
            "scope": "private",
            "created_by": "other-user",
            "python_version": "3.11",
        },
    }

    with (
        patch.object(service, "_authorize_worker", AsyncMock(return_value=(worker, "normal-user", False))),
        patch.object(runtime_control_service, "get_env", AsyncMock(return_value=env)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service._setup_worker_environment(
                _project(),
                _existing_request(),
                user_id=10,
                conn=object(),
            )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_can_bind_existing_private_runtime() -> None:
    service = ProjectService()
    project = _project()
    worker = SimpleNamespace(id=7, name="worker-security-test")
    env = {
        "success": True,
        "data": {
            "name": "private-existing",
            "scope": "private",
            "created_by": "other-user",
            "python_version": "3.11",
        },
    }

    with (
        patch.object(service, "_authorize_worker", AsyncMock(return_value=(worker, "admin", True))),
        patch.object(runtime_control_service, "get_env", AsyncMock(return_value=env)),
    ):
        await service._setup_worker_environment(
            project,
            _existing_request(),
            user_id=1,
            conn=object(),
        )

    assert project.worker_env_name == "private-existing"
    project.save.assert_awaited_once()
