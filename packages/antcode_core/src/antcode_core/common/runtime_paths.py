"""Runtime path helpers that do not instantiate application settings."""

from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    """Find the repository or deployment root from a source path."""
    current = (start or Path(__file__)).resolve()
    for parent in current.parents:
        if (parent / ".env").exists():
            return parent

    root = None
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            root = parent

    return root or Path.cwd().resolve()


def project_data_root() -> Path:
    """Return the only allowed root for generated runtime data."""
    return find_project_root() / "data"


def ensure_runtime_dir(*parts: str) -> Path:
    """Create and return a runtime directory under the root data directory."""
    path = project_data_root().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
