from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_MODEL = REPO_ROOT / "packages" / "antcode_core" / "src" / "antcode_core" / "domain" / "models" / "runtime.py"
PROJECT_MODEL = REPO_ROOT / "packages" / "antcode_core" / "src" / "antcode_core" / "domain" / "models" / "project.py"


def test_runtime_model_uses_runtime_table_names() -> None:
    content = RUNTIME_MODEL.read_text(encoding="utf-8")

    assert 'table = "venvs"' not in content
    assert 'table = "project_venv_bindings"' not in content
    assert 'table = "runtimes"' in content
    assert 'table = "project_runtime_bindings"' in content


def test_runtime_model_uses_unified_runtime_fields() -> None:
    content = RUNTIME_MODEL.read_text(encoding="utf-8")

    assert "venv_path" not in content
    assert "runtime_locator" in content
    assert "runtime_kind" in content
    assert "runtime_details" in content


def test_project_model_uses_runtime_locator() -> None:
    content = PROJECT_MODEL.read_text(encoding="utf-8")

    assert "venv_path" not in content
    assert "runtime_locator" in content
    assert "runtime_kind" in content
