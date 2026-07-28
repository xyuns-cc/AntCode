import ast
import json
import tomllib
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
SECURITY_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "security-scan.yml"
DOCKER_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "docker-build.yml"
TRIVY_IGNORE_PATH = ROOT / ".trivyignore.yaml"
PYPROJECT_PATH = ROOT / "pyproject.toml"
MAKEFILE_PATH = ROOT / "Makefile"
TESTS_README_PATH = ROOT / "tests" / "README.md"
MIGRATION_TEST_PATH = ROOT / "tests/integration/postgres/test_20260713_migrations.py"
SCHEDULER_TEST_PATH = ROOT / "tests/unit/master/test_scheduler_concurrency_stats.py"
FRONTEND_LOCK_PATH = ROOT / "web/antcode-frontend/package-lock.json"
FRONTEND_PACKAGE_PATH = ROOT / "web/antcode-frontend/package.json"
FRONTEND_SOURCE_PATH = ROOT / "web/antcode-frontend/src"
NPM_AUDIT_GATE_PATH = ROOT / "scripts/check_npm_audit.mjs"
TRIVY_GATE_COUNT = 4


def _workflow() -> dict:
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


def _step_command(job: dict, step_name: str) -> str:
    for step in job["steps"]:
        if step.get("name") == step_name:
            return str(step.get("run", ""))
    raise AssertionError(f"CI step not found: {step_name}")


def test_backend_jobs_install_dev_extra() -> None:
    jobs = _workflow()["jobs"]

    for job_name in ("backend-lint", "backend-test", "backend-security", "e2e"):
        command = _step_command(jobs[job_name], "Install dependencies")
        assert "uv sync --all-packages --extra dev" in command
        # 构建必须严格按 uv.lock 复现，禁止 CI 里隐式重解析依赖
        assert "--frozen" in command
        assert "uv sync --all-packages --dev" not in command


def test_backend_tests_run_real_redis_contracts_and_integrations() -> None:
    job = _workflow()["jobs"]["backend-test"]
    redis_service = job["services"]["redis"]
    postgres_service = job["services"]["postgres"]

    assert "@sha256:" in redis_service["image"]
    assert redis_service["ports"] == ["16379:6379"]
    assert postgres_service["env"]["POSTGRES_DB"] == "antcode_migration_test"
    contract_step = _step_command(job, "Run contract tests")
    prepare_step = _step_command(job, "Prepare integration databases")
    integration_step = _step_command(job, "Run integration tests")
    assert "pytest tests/contracts -q" in contract_step
    assert "CREATE DATABASE antcode_e2e_test" in prepare_step
    assert "scripts.init_db import _generate_schemas" in prepare_step
    assert "asyncio.run(_generate_schemas())" in prepare_step
    assert "pytest tests/integration -q" in integration_step
    assert job["env"]["DATABASE_URL"].endswith("/antcode_e2e_test")
    assert job["env"]["REDIS_URL"].endswith("/14")
    integration_env = next(step["env"] for step in job["steps"] if step.get("name") == "Run integration tests")
    assert integration_env["TEST_DATABASE_URL"].endswith("/antcode_migration_test")
    assert integration_env["DATABASE_URL"].endswith("/antcode_e2e_test")


def test_make_and_documented_test_targets_use_directories() -> None:
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    readme = TESTS_README_PATH.read_text(encoding="utf-8")

    assert "test-unit:\n\tuv run pytest tests/unit -v" in makefile
    assert "ANTCODE_INTEGRATION_REDIS_URL must be set" in makefile
    assert "DATABASE_URL must be set" in makefile
    assert "TEST_DATABASE_URL must be set" in makefile
    assert "\tuv run pytest tests/integration -v" in makefile
    assert "uv run pytest tests/unit/" in readme
    assert "uv run pytest tests/integration/ -v" in readme
    assert "tests/integration/worker/ -m integration" not in readme


def test_pytest_uses_strict_registered_markers() -> None:
    options = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    markers = tuple(options["markers"])

    assert options["addopts"] == "--strict-markers"
    for marker in ("integration", "e2e", "pbt", "transport", "loadtest_scenario", "loadtest_write"):
        assert any(definition.startswith(f"{marker}:") or definition.startswith(f"{marker}(") for definition in markers)


def test_pip_audit_uses_exported_third_party_lock() -> None:
    job = _workflow()["jobs"]["backend-security"]
    command = _step_command(job, "Run pip-audit (deps CVE scan)")
    bandit_gate = _step_command(job, "Block on HIGH severity bandit findings")
    audit_gate = _step_command(job, "Block on HIGH/CRITICAL pip-audit findings")

    # P1-CI-06: --all-packages 导出整个 workspace 的依赖闭包（根包只有 98/141）。
    assert "uv export --locked --all-packages --extra dev --no-emit-workspace" in command
    assert "pip-audit --strict --no-deps --disable-pip" in command
    assert "--requirement audit-requirements.txt" in command
    assert bandit_gate.startswith("uv run --extra dev python ")
    assert audit_gate.startswith("uv run --extra dev python ")


def test_frontend_lock_uses_only_official_npm_registry() -> None:
    lock = json.loads(FRONTEND_LOCK_PATH.read_text(encoding="utf-8"))
    resolved = {
        name: package["resolved"]
        for name, package in lock["packages"].items()
        if isinstance(package, dict) and "resolved" in package
    }

    assert lock["lockfileVersion"] == 3
    assert resolved
    assert all(url.startswith("https://registry.npmjs.org/") for url in resolved.values())


def test_frontend_install_does_not_execute_lockfile_external_npx() -> None:
    package = json.loads(FRONTEND_PACKAGE_PATH.read_text(encoding="utf-8"))
    preinstall = str(package.get("scripts", {}).get("preinstall", ""))

    assert "npx" not in preinstall
    assert "only-allow" not in preinstall


def test_trivy_rsc_advisory_exception_is_narrow_and_expires() -> None:
    policy = yaml.safe_load(TRIVY_IGNORE_PATH.read_text(encoding="utf-8"))
    assert policy == {
        "vulnerabilities": [
            {
                "id": "GHSA-qwww-vcr4-c8h2",
                "paths": ["web/antcode-frontend/package-lock.json"],
                "expired_at": date(2026, 8, 31),
                "statement": (
                    "Not affected: AntCode is a React 18 SPA using react-router-dom browser APIs. "
                    "The advisory explicitly affects only unstable React Server Components APIs, "
                    "which are not imported or enabled. Re-evaluate before this exception expires."
                ),
            }
        ]
    }
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in FRONTEND_SOURCE_PATH.rglob("*") if path.suffix in {".ts", ".tsx"}
    )
    assert "react-server" not in source
    assert "unstable_RSC" not in source
    assert "RSCHydratedRouter" not in source
    assert "RSCStaticRouter" not in source


def test_every_trivy_gate_loads_the_structured_exception() -> None:
    workflows = (
        yaml.safe_load(SECURITY_WORKFLOW_PATH.read_text(encoding="utf-8")),
        yaml.safe_load(DOCKER_WORKFLOW_PATH.read_text(encoding="utf-8")),
    )
    trivy_steps = [
        step
        for workflow in workflows
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")
    ]
    assert len(trivy_steps) == TRIVY_GATE_COUNT
    assert all(step["with"]["trivyignores"] == ".trivyignore.yaml" for step in trivy_steps)


def test_npm_audit_gate_is_precise_fail_closed_and_expires() -> None:
    job = _workflow()["jobs"]["frontend-security"]
    collect_step = _step_command(job, "Collect npm audit report")
    gate_step = _step_command(job, "Block unapproved HIGH/CRITICAL npm findings")
    gate = NPM_AUDIT_GATE_PATH.read_text(encoding="utf-8")

    assert "npm audit --json --audit-level=high" in collect_step
    assert "npm-audit-report.json" in collect_step
    assert "check_npm_audit.mjs" in gate_step
    assert "GHSA-qwww-vcr4-c8h2" in gate
    assert 'const EXPIRES_ON = "2026-08-31"' in gate
    assert 'new Set(["high", "critical"])' in gate
    assert "auditReportVersion !== 2" in gate


def test_click_security_floor_is_pinned() -> None:
    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")

    assert '"click>=8.3.3"' in pyproject


def test_postgres_migration_tests_are_unconditionally_collected() -> None:
    tree = ast.parse(MIGRATION_TEST_PATH.read_text(encoding="utf-8"))
    top_level_tests = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }

    assert {
        "test_fresh_database_init_creates_current_schema",
        "test_legacy_schema_upgrade_is_idempotent_and_preserves_data",
        "test_failed_sql_migration_rolls_back_schema_and_preserves_data",
        "test_install_key_data_migration_rolls_back_then_retries_idempotently",
    } <= top_level_tests


def test_scheduler_concurrency_test_cannot_skip_missing_production_modules() -> None:
    source = SCHEDULER_TEST_PATH.read_text(encoding="utf-8")

    assert "pytest.importorskip" not in source
    assert "importlib.import_module(module_path)" in source


def test_summary_job_fails_when_any_required_job_did_not_succeed() -> None:
    summary_job = _workflow()["jobs"]["summary"]
    summary_step = next(step for step in summary_job["steps"] if step["name"] == "Summary")
    command = summary_step["run"]

    assert summary_job["if"] == "always()"
    assert summary_step["env"] == {
        "BACKEND_LINT_RESULT": "${{ needs.backend-lint.result }}",
        "BACKEND_TEST_RESULT": "${{ needs.backend-test.result }}",
        "E2E_RESULT": "${{ needs.e2e.result }}",
        "BACKEND_SECURITY_RESULT": "${{ needs.backend-security.result }}",
        "WINDOWS_ARTIFACT_RESULT": "${{ needs.windows-artifact-security.result }}",
        "FRONTEND_TEST_RESULT": "${{ needs.frontend-test.result }}",
        "FRONTEND_SECURITY_RESULT": "${{ needs.frontend-security.result }}",
        "FRONTEND_BUILD_RESULT": "${{ needs.frontend-build.result }}",
    }
    assert 'if [ "${check#*=}" != "success" ]' in command
    assert "exit 1" in command


def test_e2e_job_really_runs_and_gates_image_publication() -> None:
    jobs = _workflow()["jobs"]
    e2e_job = jobs["e2e"]
    run_step = _step_command(e2e_job, "Run E2E tests")
    init_step = _step_command(e2e_job, "Initialize database schema")
    stack_step = _step_command(e2e_job, "Start control plane stack")
    install_key_step = _step_command(e2e_job, "Generate Direct Worker install key")
    worker_step = _step_command(e2e_job, "Start Direct Worker")
    secret_step = _step_command(e2e_job, "Generate ephemeral stack secrets")
    diagnostics_step = _step_command(e2e_job, "Dump stack diagnostics on failure")

    # E2E 门禁必须真实执行（不是 --collect-only），且跑在 compose 全栈之上
    assert "pytest tests/e2e -q" in run_step
    assert "--collect-only" not in run_step
    assert "docker-compose.dev.yml" in str(e2e_job["env"]["COMPOSE_ARGS"])
    # P1-CI-03: fresh 栈必须先显式建表（web_api lifespan 不建表）。
    assert "scripts.init_db" in init_step
    assert "docker compose" in stack_step
    assert "/workers/generate-install-key" in install_key_step
    assert "::add-mask::" in install_key_step
    assert "docker compose" in worker_step and " worker" in worker_step
    assert "::add-mask::" in secret_step
    assert "annotate_e2e_diagnostics.py" in diagnostics_step
    # P1-CI-04: 必须有真实走 Gateway 认证/lease 链路的冒烟步骤。
    gateway_smoke = _step_command(e2e_job, "Run gateway backendless worker smoke")
    assert "WORKER_TRANSPORT_MODE=gateway" in gateway_smoke
    assert "WORKER_GATEWAY_BACKENDLESS=true" in gateway_smoke
    # 镜像发布与分支保护 summary 都必须被真实 E2E 阻断
    assert "e2e" in jobs["publish-images"]["needs"]
    assert "e2e" in jobs["summary"]["needs"]


def test_windows_security_job_publishes_actionable_failure_annotations() -> None:
    job = _workflow()["jobs"]["windows-artifact-security"]
    test_step = _step_command(job, "Run Windows artifact security tests")
    annotation_step = _step_command(job, "Annotate Windows artifact test failures")

    assert "--junitxml=windows-artifact-results.xml" in test_step
    assert "${{ github.run_id }}" in job["env"]["DATABASE_URL"]
    assert job["env"]["REDIS_URL"] == "redis://localhost:6379/0"
    assert "windows-artifact-results.xml" in annotation_step
    assert "::error title=Windows artifact test failed:" in annotation_step
