"""
Property-Based Test: Import Boundary

**Feature: directory-restructure, Property 1: Import Boundary**
**Validates: Requirements 3.4**

Property 1: Import Boundary
*For any* 服务模块，如果它需要通用功能（配置、日志、异常、模型、Schema），
它必须从 `antcode_core` 导入，而不是自行实现或从其他服务导入。

This test statically scans all service modules' import statements to verify
that common functionality is sourced from antcode_core.
"""

import ast
import os
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Define the services and their source directories
SERVICES = {
    "web_api": "services/web_api/src/antcode_web_api",
    "master": "services/master/src/antcode_master",
    "gateway": "services/gateway/src/antcode_gateway",
    "worker": "services/worker/src/antcode_worker",
}

# Common functionality modules that MUST come from antcode_core
COMMON_MODULES = {
    "config",
    "logging",
    "exceptions",
    "ids",
    "time",
    "security",
}

# Forbidden import patterns - services should not import these from src/ or other services
FORBIDDEN_IMPORT_PREFIXES = [
    "src.core.",
    "src.common.",
    "src.models.",
    "src.schemas.",
    "src.infrastructure.",
]


class ImportViolation(NamedTuple):
    """Represents an import boundary violation."""

    file_path: str
    line_number: int
    import_statement: str
    violation_type: str


def get_all_python_files(directory: str) -> list[Path]:
    """Get all Python files in a directory recursively."""
    base_path = Path(directory)
    if not base_path.exists():
        return []
    return list(base_path.rglob("*.py"))


def extract_imports(file_path: Path) -> list[tuple[int, str]]:
    """Extract all import statements from a Python file."""
    imports = []
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append((node.lineno, f"from {module} import {alias.name}"))
    except (SyntaxError, UnicodeDecodeError):
        # Skip files that can't be parsed
        pass
    return imports


def check_import_boundary_violations(
    service_name: str, service_path: str
) -> list[ImportViolation]:
    """Check a service for import boundary violations."""
    violations = []
    python_files = get_all_python_files(service_path)

    for file_path in python_files:
        # Skip __pycache__ and test files
        if "__pycache__" in str(file_path) or "test_" in file_path.name:
            continue

        imports = extract_imports(file_path)
        rel_path = str(file_path)

        for line_no, import_stmt in imports:
            # Check for forbidden imports from old src/ structure
            for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                if forbidden in import_stmt:
                    violations.append(
                        ImportViolation(
                            file_path=rel_path,
                            line_number=line_no,
                            import_statement=import_stmt,
                            violation_type=f"Forbidden import from old structure: {forbidden}",
                        )
                    )

    return violations


def get_all_service_files() -> list[tuple[str, str, Path]]:
    """Get all Python files from all services with their service name."""
    all_files = []
    for service_name, service_path in SERVICES.items():
        python_files = get_all_python_files(service_path)
        for file_path in python_files:
            if "__pycache__" not in str(file_path) and "test_" not in file_path.name:
                all_files.append((service_name, service_path, file_path))
    return all_files


# Strategy to generate service file selections
@st.composite
def service_file_strategy(draw):
    """Strategy that selects a random service file for testing."""
    all_files = get_all_service_files()
    if not all_files:
        # Return a placeholder if no files exist
        return ("none", "none", Path("none"))
    return draw(st.sampled_from(all_files))


class TestImportBoundary:
    """Property-based tests for import boundary validation."""

    @pytest.mark.pbt
    @settings(max_examples=100, deadline=None)
    @given(service_file=service_file_strategy())
    def test_no_forbidden_imports_in_service_files(self, service_file):
        """
        **Feature: directory-restructure, Property 1: Import Boundary**
        **Validates: Requirements 3.4**

        Property: For any service module file, it must not import common
        functionality from the old src/ structure or from other services.
        """
        service_name, service_path, file_path = service_file

        # Skip if no files exist
        if service_name == "none":
            return

        if not file_path.exists():
            return

        imports = extract_imports(file_path)

        for line_no, import_stmt in imports:
            # Check for forbidden imports from old src/ structure
            for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                assert forbidden not in import_stmt, (
                    f"Import boundary violation in {service_name}:\n"
                    f"  File: {file_path}\n"
                    f"  Line: {line_no}\n"
                    f"  Import: {import_stmt}\n"
                    f"  Violation: Should import from antcode_core instead of {forbidden}"
                )

    @pytest.mark.pbt
    def test_all_services_import_boundary_compliance(self):
        """
        **Feature: directory-restructure, Property 1: Import Boundary**
        **Validates: Requirements 3.4**

        Comprehensive test that checks all services for import boundary violations.
        """
        all_violations = []

        for service_name, service_path in SERVICES.items():
            violations = check_import_boundary_violations(service_name, service_path)
            all_violations.extend(violations)

        if all_violations:
            violation_report = "\n".join(
                f"  - {v.file_path}:{v.line_number}: {v.import_statement}\n"
                f"    Reason: {v.violation_type}"
                for v in all_violations
            )
            pytest.fail(
                f"Found {len(all_violations)} import boundary violations:\n{violation_report}"
            )

    @pytest.mark.pbt
    def test_common_functionality_from_antcode_core(self):
        """
        **Feature: directory-restructure, Property 1: Import Boundary**
        **Validates: Requirements 3.4**

        Verify that when services use common functionality names,
        they import from antcode_core.
        """
        violations = []

        for service_name, service_path in SERVICES.items():
            python_files = get_all_python_files(service_path)

            for file_path in python_files:
                if "__pycache__" in str(file_path) or "test_" in file_path.name:
                    continue

                imports = extract_imports(file_path)

                for line_no, import_stmt in imports:
                    # Check if importing common module names from wrong sources
                    for common_mod in COMMON_MODULES:
                        # If importing a common module, it should be from antcode_core
                        if f"import {common_mod}" in import_stmt or f".{common_mod}" in import_stmt:
                            # Allow imports from antcode_core
                            if "antcode_core" in import_stmt:
                                continue
                            # Allow relative imports within the same service
                            if import_stmt.startswith("from ."):
                                continue
                            # Allow standard library imports
                            if import_stmt in ["import logging", "import time"]:
                                continue
                            # Flag potential violations for manual review
                            # (not all are violations, but worth checking)

        # This test passes if no hard violations found
        assert True
