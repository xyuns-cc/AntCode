from types import SimpleNamespace

from antcode_core.application.services.projects.project_service import ProjectService
from antcode_core.domain.schemas.project import ProjectFileUpdateRequest


def test_file_detail_payload_preserves_existing_language_when_omitted() -> None:
    service = ProjectService()
    current = SimpleNamespace(
        language="go",
        entry_point="main.go",
        runtime_config={},
        environment_vars={},
    )

    payload = service._build_file_detail_payload(ProjectFileUpdateRequest(), current_detail=current)

    assert payload["language"] == "go"


def test_file_detail_payload_updates_language_explicitly() -> None:
    service = ProjectService()
    current = SimpleNamespace(
        language="python",
        entry_point="main.py",
        runtime_config={},
        environment_vars={},
    )

    payload = service._build_file_detail_payload(
        ProjectFileUpdateRequest(language="node"),
        current_detail=current,
    )

    assert payload["language"] == "node"
