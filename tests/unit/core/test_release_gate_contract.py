"""锁住 `make release-gate` 的范围与它明确不覆盖的部分。

这套断言存在的理由是两个真实故障：
1. `pyproject.toml` 没有 `testpaths` 时，裸 `pytest` 会收集 `tests/e2e`（需要完整容器栈）
   和运行期产物目录 `data/`（Worker 跑过后 `data/worker/egress` 是 0300），于是
   `make test` —— 也就是 `make release-gate` 的后端测试门禁 —— 在开发机上根本跑不完。
2. `make audit-npm` 跟随本机 npm registry 时，国内镜像不实现 audit 接口，报告缺
   `auditReportVersion`，门禁只会报 malformed，永远验不到真实漏洞。
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_PATH = ROOT / "pyproject.toml"
MAKEFILE_PATH = ROOT / "Makefile"
GITIGNORE_PATH = ROOT / ".gitignore"
RUNBOOK_PATH = ROOT / "docs/release-runbook.md"

LOCAL_TEST_PATHS = ["tests/unit", "tests/boundary"]
EXTERNAL_DEPENDENCY_SUITES = ("tests/contracts", "tests/integration", "tests/e2e", "tests/loadtest")
OFFICIAL_NPM_REGISTRY = "https://registry.npmjs.org"
RELEASE_GATE_PREREQUISITES = ("proto-check", "check", "test", "audit", "web-check")
AUDIT_ARTIFACTS = ("/bandit-report.json", "/pip-audit-report.json", "/audit-requirements.txt")


def _pytest_options() -> dict[str, object]:
    payload = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return payload["tool"]["pytest"]["ini_options"]


def _makefile_recipe(target: str) -> str:
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(f"{target}:"))
    recipe = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line)
    return "\n".join(recipe)


def test_bare_pytest_only_collects_suites_without_external_dependencies() -> None:
    assert _pytest_options()["testpaths"] == LOCAL_TEST_PATHS


def test_testpaths_excludes_runtime_artifacts_and_middleware_suites() -> None:
    testpaths = _pytest_options()["testpaths"]
    assert all(not path.startswith("data") for path in testpaths)
    for suite in EXTERNAL_DEPENDENCY_SUITES:
        assert suite not in testpaths


def test_release_gate_runs_the_full_local_gate() -> None:
    recipe = _makefile_recipe("release-gate")
    prerequisites = recipe.splitlines()[0].split(":", 1)[1].split()
    assert tuple(prerequisites) == RELEASE_GATE_PREREQUISITES


def test_middleware_suites_have_dedicated_targets() -> None:
    assert "uv run pytest tests/contracts" in _makefile_recipe("test-contracts")
    assert "uv run pytest tests/integration" in _makefile_recipe("test-int")


def test_npm_audit_pins_the_official_registry() -> None:
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert f"NPM_AUDIT_REGISTRY := {OFFICIAL_NPM_REGISTRY}" in makefile
    assert "--registry=$(NPM_AUDIT_REGISTRY)" in _makefile_recipe("audit-npm")


def test_audit_reports_are_never_committed() -> None:
    ignored = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    for artifact in AUDIT_ARTIFACTS:
        assert artifact in ignored


def test_runbook_documents_the_suites_release_gate_cannot_run() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    for suite in EXTERNAL_DEPENDENCY_SUITES:
        assert suite in runbook
