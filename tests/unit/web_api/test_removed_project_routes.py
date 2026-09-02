from pathlib import Path

from antcode_web_api.routes.v1 import project as project_route
from antcode_web_api.routes.v1 import v1_router
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[3]


REMOVED_ROUTE_SUFFIXES = (
    "/download",
    "/worker-download",
    "/incremental-sync",
    "/publish",
    "/discard",
    "/versions",
    "/draft/status",
    "/draft/files/{file_path:path}",
    "/files/structure",
    "/files/content",
    "/files/download",
)


def test_removed_project_routes_are_not_registered():
    app = FastAPI()
    app.include_router(v1_router)
    route_paths = {path for path in app.openapi()["paths"] if path.startswith("/projects/")}

    for suffix in REMOVED_ROUTE_SUFFIXES:
        assert all(not path.endswith(suffix) for path in route_paths)


def test_removed_project_route_modules_are_deleted():
    route_dir = ROOT / "services/web_api/src/antcode_web_api/routes/v1"
    removed_modules = (
        "project_download.py",
        "project_sync.py",
        "project_versions.py",
    )

    for filename in removed_modules:
        assert not (route_dir / filename).exists()


def test_removed_project_file_workspace_service_is_deleted():
    service = ROOT / "services/web_api/src/antcode_web_api/services/projects/project_file_service.py"

    assert not service.exists()


def test_create_form_rejects_removed_code_content_field():
    assert "code_content" not in project_route.CREATE_PROJECT_FORM_FIELDS


def test_removed_file_content_schemas_are_not_exposed():
    source = (ROOT / "packages/antcode_core/src/antcode_core/domain/schemas/project.py").read_text(encoding="utf-8")

    assert "ProjectFileContentUpdateRequest" not in source
    assert "FileStructureResponse" not in source
    assert "FileContentResponse" not in source


def test_unified_code_update_does_not_accept_inline_content():
    source = (ROOT / "packages/antcode_core/src/antcode_core/domain/schemas/project_unified.py").read_text(
        encoding="utf-8"
    )

    assert "content: str | None" not in source
    assert '"content"' not in source
    assert '"changelog"' not in source


def test_frontend_project_contract_has_no_legacy_source_fields():
    frontend = ROOT / "web/antcode-frontend/src"
    source = "\n".join(
        [
            (frontend / "types/project.ts").read_text(encoding="utf-8"),
            (frontend / "components/projects/ProjectEditDrawer.tsx").read_text(encoding="utf-8"),
        ]
    )

    for field in ("source_type", "code_content", "git_branch", "git_commit", "git_subdir"):
        assert field not in source


def test_frontend_does_not_call_removed_project_file_routes():
    frontend = ROOT / "web/antcode-frontend/src"
    checked_files = [
        frontend / "services/projects.ts",
        frontend / "pages/Projects/index.tsx",
        frontend / "pages/Projects/ProjectDetail.tsx",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in checked_files)

    assert "files/structure" not in source
    assert "files/content" not in source
    assert "files/download" not in source
    assert "ProjectFileManager" not in source
    assert "FileViewer" not in source
