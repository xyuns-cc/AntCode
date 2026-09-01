from pathlib import Path

from antcode_core.domain.models.project import ProjectFile
from antcode_core.domain.schemas.project import FileInfo

REPO_ROOT = Path(__file__).parents[3]


def test_legacy_code_source_helper_is_deleted():
    helper = REPO_ROOT / "packages/antcode_core/src/antcode_core/application/services/projects/code_source.py"

    assert not helper.exists()


def test_project_service_does_not_embed_source_in_runtime_config():
    source = (
        REPO_ROOT / "packages/antcode_core/src/antcode_core/application/services/projects/project_service.py"
    ).read_text(encoding="utf-8")

    assert 'runtime_config["source"]' not in source
    assert 'runtime_config.get("source")' not in source
    assert "build_code_source_config" not in source
    assert "_get_or_create_repository" not in source


def test_file_info_response_has_repository_source_fields_only():
    fields = set(FileInfo.model_fields)

    assert {"repository_id", "ref", "subdir", "include_paths"}.issubset(fields)
    assert "source_name" not in fields
    assert "git_url" not in fields
    assert "git_credential_id" not in fields
    assert "file_hash" not in fields
    assert "file_path" not in fields
    assert "original_file_path" not in fields


def test_project_file_model_has_no_source_mirror_fields():
    fields = set(ProjectFile._meta.fields_map)

    assert "entry_point" in fields
    assert "runtime_config" in fields
    assert "environment_vars" in fields
    assert "source_url" not in fields
    assert "source_subdir" not in fields
    assert "source_revision" not in fields
    assert "source_name" not in fields


def test_project_source_model_contains_only_git_binding_fields():
    from antcode_core.domain.models import ProjectSource

    fields = set(ProjectSource._meta.fields_map)
    assert {"project_id", "repository_id", "ref", "subdir", "include_paths"}.issubset(fields)
    assert "entry_point" not in fields
    assert "runtime_config" not in fields


def test_repository_subdir_can_back_multiple_projects():
    from antcode_core.domain.models import ProjectSource

    unique_together = getattr(ProjectSource.Meta, "unique_together", ())
    assert ("repository_id", "subdir") not in unique_together


def test_project_service_never_writes_project_file_source_mirrors():
    source = (
        REPO_ROOT / "packages/antcode_core/src/antcode_core/application/services/projects/project_service.py"
    ).read_text(encoding="utf-8")

    assert "source_url" not in source
    assert "source_subdir" not in source
    assert "source_revision" not in source
    assert "source_name" not in source
