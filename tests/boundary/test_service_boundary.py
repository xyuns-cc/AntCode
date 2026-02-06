"""
Property-Based Test: Service Boundary Guard

**Feature: directory-restructure, Property 2: Service Boundary Guard**
**Validates: Requirements 4.5, 5.4**

Property 2: Service Boundary Guard
*For any* 服务包，以下导入规则必须成立：
- web_api 不得 import master/gateway/worker 包
- master 不得 import web_api/gateway/worker 包
- gateway 不得 import master/web_api 包
- worker 不得 import 任何 service 包

This test verifies that services maintain proper isolation and do not
import from other services directly.
"""

import ast
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

# Define forbidden cross-service imports for each service
# Key: service name, Value: list of service package names it cannot import
FORBIDDEN_IMPORTS = {
    "web_api": ["antcode_master", "antcode_gateway", "antcode_worker"],
    "master": ["antcode_web_api", "antcode_gateway", "antcode_worker"],
    "gateway": ["antcode_master", "antcode_web_api"],
    "worker": ["antcode_web_api", "antcode_master", "antcode_gateway"],
}


class ServiceBoundaryViolation(NamedTuple):
    """Represents a service boundary violation."""

    source_service: str
    file_path: str
    line_number: int
    import_statement: str
    target_service: str


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


def check_service_boundary_violations(
    service_name: str, service_path: str
) -> list[ServiceBoundaryViolation]:
    """Check a service for cross-service import violations."""
    violations = []
    forbidden = FORBIDDEN_IMPORTS.get(service_name, [])
    python_files = get_all_python_files(service_path)

    for file_path in python_files:
        # Skip __pycache__ and test files
        if "__pycache__" in str(file_path) or "test_" in file_path.name:
            continue

        imports = extract_imports(file_path)
        rel_path = str(file_path)

        for line_no, import_stmt in imports:
            for forbidden_pkg in forbidden:
                if forbidden_pkg in import_stmt:
                    violations.append(
                        ServiceBoundaryViolation(
                            source_service=service_name,
                            file_path=rel_path,
                            line_number=line_no,
                            import_statement=import_stmt,
                            target_service=forbidden_pkg,
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
        return ("none", "none", Path("none"))
    return draw(st.sampled_from(all_files))


class TestServiceBoundary:
    """Property-based tests for service boundary validation."""

    @pytest.mark.pbt
    @settings(max_examples=100, deadline=None)
    @given(service_file=service_file_strategy())
    def test_no_cross_service_imports(self, service_file):
        """
        **Feature: directory-restructure, Property 2: Service Boundary Guard**
        **Validates: Requirements 4.5, 5.4**

        Property: For any service module file, it must not import from
        other service packages that are forbidden by the boundary rules.
        """
        service_name, service_path, file_path = service_file

        # Skip if no files exist
        if service_name == "none":
            return

        if not file_path.exists():
            return

        forbidden = FORBIDDEN_IMPORTS.get(service_name, [])
        imports = extract_imports(file_path)

        for line_no, import_stmt in imports:
            for forbidden_pkg in forbidden:
                assert forbidden_pkg not in import_stmt, (
                    f"Service boundary violation in {service_name}:\n"
                    f"  File: {file_path}\n"
                    f"  Line: {line_no}\n"
                    f"  Import: {import_stmt}\n"
                    f"  Violation: {service_name} cannot import from {forbidden_pkg}"
                )

    @pytest.mark.pbt
    def test_web_api_boundary(self):
        """
        **Feature: directory-restructure, Property 2: Service Boundary Guard**
        **Validates: Requirements 4.5**

        web_api 不得 import master/gateway/worker 包
        """
        violations = check_service_boundary_violations("web_api", SERVICES["web_api"])

        if violations:
            violation_report = "\n".join(
                f"  - {v.file_path}:{v.line_number}: {v.import_statement}\n"
                f"    Cannot import from: {v.target_service}"
                for v in violations
            )
            pytest.fail(
                f"web_api has {len(violations)} service boundary violations:\n{violation_report}"
            )

    @pytest.mark.pbt
    def test_master_boundary(self):
        """
        **Feature: directory-restructure, Property 2: Service Boundary Guard**
        **Validates: Requirements 5.4**

        master 不得 import web_api/gateway/worker 包
        """
        violations = check_service_boundary_violations("master", SERVICES["master"])

        if violations:
            violation_report = "\n".join(
                f"  - {v.file_path}:{v.line_number}: {v.import_statement}\n"
                f"    Cannot import from: {v.target_service}"
                for v in violations
            )
            pytest.fail(
                f"master has {len(violations)} service boundary violations:\n{violation_report}"
            )

    @pytest.mark.pbt
    def test_gateway_boundary(self):
        """
        **Feature: directory-restructure, Property 2: Service Boundary Guard**
        **Validates: Requirements 4.5, 5.4**

        gateway 不得 import master/web_api 包
        """
        violations = check_service_boundary_violations("gateway", SERVICES["gateway"])

        if violations:
            violation_report = "\n".join(
                f"  - {v.file_path}:{v.line_number}: {v.import_statement}\n"
                f"    Cannot import from: {v.target_service}"
                for v in violations
            )
            pytest.fail(
                f"gateway has {len(violations)} service boundary violations:\n{violation_report}"
            )

    @pytest.mark.pbt
    def test_worker_boundary(self):
        """
        **Feature: directory-restructure, Property 2: Service Boundary Guard**
        **Validates: Requirements 4.5, 5.4**

        worker 不得 import 任何 service 包
        """
        violations = check_service_boundary_violations("worker", SERVICES["worker"])

        if violations:
            violation_report = "\n".join(
                f"  - {v.file_path}:{v.line_number}: {v.import_statement}\n"
                f"    Cannot import from: {v.target_service}"
                for v in violations
            )
            pytest.fail(
                f"worker has {len(violations)} service boundary violations:\n{violation_report}"
            )

    @pytest.mark.pbt
    def test_all_services_boundary_compliance(self):
        """
        **Feature: directory-restructure, Property 2: Service Boundary Guard**
        **Validates: Requirements 4.5, 5.4**

        Comprehensive test that checks all services for boundary violations.
        """
        all_violations = []

        for service_name, service_path in SERVICES.items():
            violations = check_service_boundary_violations(service_name, service_path)
            all_violations.extend(violations)

        if all_violations:
            violation_report = "\n".join(
                f"  - [{v.source_service}] {v.file_path}:{v.line_number}: {v.import_statement}\n"
                f"    Cannot import from: {v.target_service}"
                for v in all_violations
            )
            pytest.fail(
                f"Found {len(all_violations)} service boundary violations:\n{violation_report}"
            )
