from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.application.services.projects.project_service import project_service
from antcode_core.domain.models import Project
from antcode_core.domain.schemas.project import (
    ProjectCreateFormRequest,
    ProjectFileCreateRequest,
    ProjectRuleCreateRequest,
    ProjectRuleUpdateRequest,
)
from antcode_web_api.routes.v1.project import _build_project_create_request, project_router
from pydantic import ValidationError


def _rule_payload() -> dict:
    return {
        "name": "rule-project",
        "type": "rule",
        "target_url": "https://example.com",
        "extraction_rules": [{"desc": "title", "type": "css", "expr": "h1"}],
    }


def test_rule_project_does_not_require_worker_runtime() -> None:
    request = ProjectRuleCreateRequest.model_validate({**_rule_payload(), "region": "cn-east", "require_render": True})

    assert request.worker_id is None
    assert request.python_version is None
    assert request.region == "cn-east"
    assert request.require_render is True


def test_rule_form_preserves_dispatch_constraints() -> None:
    form = ProjectCreateFormRequest(
        name="rule-project",
        type="rule",
        runtime_scope="shared",
        target_url="https://example.com",
        extraction_rules='[{"desc":"title","type":"css","expr":"h1"}]',
        region="cn-east",
        require_render=True,
    )

    request = _build_project_create_request(form)

    assert isinstance(request, ProjectRuleCreateRequest)
    assert request.region == "cn-east"
    assert request.require_render is True


def test_rule_update_can_clear_region_and_disable_explicit_render_requirement() -> None:
    request = ProjectRuleUpdateRequest(region=None, require_render=False)

    payload = project_service._build_rule_update_payload(request)

    assert payload == {"region": None, "require_render": False}


def test_rule_update_can_clear_json_configuration_and_disable_resume() -> None:
    request = ProjectRuleUpdateRequest(
        headers={},
        cookies={},
        proxy_config={},
        anti_spider={},
        task_config={},
        dedup_config={},
        resume_enabled=False,
    )

    payload = project_service._build_rule_update_payload(request)

    assert payload == {
        "resume_enabled": False,
        "headers": {},
        "cookies": {},
        "proxy_config": {},
        "anti_spider": {},
        "task_config": {},
        "dedup_config": {},
    }


def test_file_project_still_requires_worker_runtime() -> None:
    payload = {
        "name": "file-project",
        "type": "file",
        "runtime_scope": "private",
        "python_version": "3.12",
        "entry_point": "main.py",
        "repository_id": "repo-1",
        "subdir": "src",
    }

    try:
        ProjectFileCreateRequest.model_validate(payload)
    except ValidationError as exc:
        assert "必须指定 worker_id" in str(exc)
    else:
        raise AssertionError("file project accepted without worker_id")


def test_file_form_preserves_language() -> None:
    form = ProjectCreateFormRequest(
        name="file-project",
        type="file",
        runtime_scope="private",
        python_version="1.24",
        worker_id="worker-1",
        entry_point="main.go",
        language="go",
        repository_id="repo-1",
        subdir="src",
    )

    request = _build_project_create_request(form)

    assert isinstance(request, ProjectFileCreateRequest)
    assert request.language == "go"


def test_rule_config_route_declares_json_request_body() -> None:
    route = next(route for route in project_router.routes if getattr(route, "path", "") == "/{project_id}/rule-config")

    assert [field.name for field in route.dependant.body_params] == ["request"]
    assert not any(field.name == "request" for field in route.dependant.query_params)


@pytest.mark.asyncio
async def test_rule_project_creation_skips_worker_environment(monkeypatch) -> None:
    class _Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    project = SimpleNamespace(id=1, name="rule-project")
    setup_environment = AsyncMock()
    monkeypatch.setattr("tortoise.transactions.in_transaction", lambda: _Transaction())
    monkeypatch.setattr(Project, "create", AsyncMock(return_value=project))
    monkeypatch.setattr(project_service, "_setup_project_environment", setup_environment)
    monkeypatch.setattr(project_service, "_create_rule_project_detail", AsyncMock())
    monkeypatch.setattr(project_service, "_attach_project_creator", AsyncMock())

    created = await project_service.create_project(ProjectRuleCreateRequest.model_validate(_rule_payload()), 7)

    assert created is project
    setup_environment.assert_not_awaited()
