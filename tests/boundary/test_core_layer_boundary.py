"""
Core Layer Boundary Guard

Ensures antcode_core layer isolation:
- domain must not import application/infrastructure or services
- application must not import services
- infrastructure must not import application or services
"""

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

CORE_PACKAGES = {
    "domain": Path("packages/antcode_core/src/antcode_core/domain"),
    "application": Path("packages/antcode_core/src/antcode_core/application"),
    "infrastructure": Path("packages/antcode_core/src/antcode_core/infrastructure"),
}

FORBIDDEN_IMPORTS = {
    "domain": [
        "antcode_core.application",
        "antcode_core.infrastructure",
        "antcode_web_api",
        "antcode_master",
        "antcode_gateway",
        "antcode_worker",
    ],
    "application": [
        "antcode_web_api",
        "antcode_master",
        "antcode_gateway",
        "antcode_worker",
    ],
    "infrastructure": [
        "antcode_core.application",
        "antcode_web_api",
        "antcode_master",
        "antcode_gateway",
        "antcode_worker",
    ],
}


class LayerViolation(NamedTuple):
    layer: str
    file_path: str
    line_number: int
    import_statement: str
    forbidden: str


def extract_imports(file_path: Path) -> list[tuple[int, str]]:
    imports = []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except (SyntaxError, UnicodeDecodeError):
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append((node.lineno, f"from {module} import {alias.name}"))
    return imports


def iter_python_files(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    return [
        path
        for path in base_dir.rglob("*.py")
        if "__pycache__" not in str(path) and "test_" not in path.name
    ]


def scan_layer_violations(layer: str, base_dir: Path) -> list[LayerViolation]:
    violations: list[LayerViolation] = []
    forbidden = FORBIDDEN_IMPORTS.get(layer, [])

    for file_path in iter_python_files(base_dir):
        for line_no, import_stmt in extract_imports(file_path):
            for forbid in forbidden:
                if forbid in import_stmt:
                    violations.append(
                        LayerViolation(
                            layer=layer,
                            file_path=str(file_path),
                            line_number=line_no,
                            import_statement=import_stmt,
                            forbidden=forbid,
                        )
                    )
    return violations


@pytest.mark.pbt
def test_core_layer_boundaries():
    violations: list[LayerViolation] = []
    for layer, base_dir in CORE_PACKAGES.items():
        violations.extend(scan_layer_violations(layer, base_dir))

    if violations:
        report = "\n".join(
            f"  - [{v.layer}] {v.file_path}:{v.line_number}: {v.import_statement}\n"
            f"    Forbidden: {v.forbidden}"
            for v in violations
        )
        pytest.fail(f"Found {len(violations)} core layer boundary violations:\n{report}")
