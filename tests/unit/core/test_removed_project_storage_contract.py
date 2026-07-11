from pathlib import Path

SRC_ROOT = Path(__file__).parents[3] / "packages" / "antcode_core" / "src"

REMOVED_PATHS = (
    "antcode_core/application/services/projects/artifact_tree.py",
    "antcode_core/application/services/projects/managed_paths.py",
    "antcode_core/application/services/projects/project_artifact_service.py",
    "antcode_core/application/services/projects/storage_paths.py",
    "antcode_core/application/services/projects/upload_stream.py",
    "antcode_core/application/services/projects/draft_service.py",
    "antcode_core/application/services/projects/version_service.py",
    "antcode_core/application/services/files/__init__.py",
    "antcode_core/infrastructure/storage/__init__.py",
)


def test_object_storage_and_workspace_modules_are_removed():
    for relative_path in REMOVED_PATHS:
        assert not (SRC_ROOT / relative_path).exists()
