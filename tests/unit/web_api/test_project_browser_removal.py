from pathlib import Path

import pytest
from antcode_core.domain.schemas.project import ProjectCreateFormRequest
from antcode_web_api.routes.v1 import project as project_route
from pydantic import ValidationError

REPO_ROOT = Path(__file__).parents[3]


def _base_payload(**overrides):
    payload = {
        "name": "Rule Demo",
        "type": "rule",
        "runtime_scope": "shared",
        "python_version": "3.11",
        "target_url": "https://example.com",
        "extraction_rules": '[{"desc":"title","type":"css","expr":"h1"}]',
    }
    payload.update(overrides)
    return payload


def test_create_form_rejects_removed_browser_engine():
    with pytest.raises(ValidationError):
        ProjectCreateFormRequest(**_base_payload(engine="browser"))


def test_create_form_accepts_rule_fields():
    form = ProjectCreateFormRequest(
        **_base_payload(
            retry_count=2,
            timeout=15,
            dont_filter="true",
            data_schema='{"fields":["title"]}',
            proxy_config='{"enabled":true,"proxy_url":"http://proxy.example.com:8080"}',
            anti_spider='{"enabled":true,"random_delay":true}',
            task_config='{"worker_id":"worker-001"}',
        )
    )

    assert form.retry_count == 2
    assert form.timeout == 15
    assert form.dont_filter is True
    assert "proxy.example.com" in (form.proxy_config or "")


@pytest.mark.parametrize(
    "legacy_field",
    [
        "browser_config",
        "source_type",
        "file_source_type",
        "code_source_type",
        "git_url",
        "git_credential_id",
        "code_content",
    ],
)
def test_create_form_rejects_removed_fields(legacy_field):
    with pytest.raises(ValidationError) as exc_info:
        ProjectCreateFormRequest(**_base_payload(**{legacy_field: "removed"}))

    assert legacy_field in str(exc_info.value)


@pytest.mark.asyncio
async def test_form_dependency_returns_validated_model():
    form = ProjectCreateFormRequest(**_base_payload())

    assert await project_route.get_project_create_form(form) is form


def test_create_form_accepts_repository_source_fields():
    form = ProjectCreateFormRequest(
        name="File Demo",
        type="file",
        runtime_scope="shared",
        python_version="3.11",
        repository_id="repo-001",
        ref="main",
        subdir="spiders/news",
        include_paths='["libs/common"]',
        entry_point="main.py",
    )

    assert form.repository_id == "repo-001"
    assert form.ref == "main"
    assert form.subdir == "spiders/news"
    assert form.include_paths == ["libs/common"]


def test_project_route_does_not_copy_project_file_source_mirrors():
    source = Path(project_route.__file__).read_text(encoding="utf-8")

    assert "source_url" not in source
    assert "source_subdir" not in source
    assert "source_revision" not in source
    assert "source_name" not in source


def test_project_route_has_no_legacy_source_contract():
    source = Path(project_route.__file__).read_text(encoding="utf-8")

    assert "normalize_source_type" not in source
    assert "get_runtime_source_config" not in source
    assert '"content": detail.content' not in source
    assert '"version": detail.version' not in source


def test_project_validation_accepts_repository_contract_only():
    fields = set(project_route.ProjectValidateRequest.model_fields)

    assert {"repository_id", "subdir", "entry_point"}.issubset(fields)
    assert "source_type" not in fields
    assert "git_url" not in fields
    assert "code_content" not in fields


def test_frontend_has_no_project_side_git_credential_ui():
    source_root = REPO_ROOT / "web/antcode-frontend/src"

    assert not (source_root / "components/projects/GitCredentialSelect.tsx").exists()
    system_config = (source_root / "pages/SystemConfig/index.tsx").read_text(encoding="utf-8")
    assert "GitCredentialsTab" not in system_config
    assert "git_credentials" not in system_config
