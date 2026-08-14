from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
CI_PATH = WORKFLOW_ROOT / "ci.yml"
SECURITY_PATH = WORKFLOW_ROOT / "security-scan.yml"
RELEASE_PATHS = (
    WORKFLOW_ROOT / "docker-build.yml",
    WORKFLOW_ROOT / "docker-build-candidate.yml",
    WORKFLOW_ROOT / "docker-release-e2e.yml",
    WORKFLOW_ROOT / "docker-finalize-release.yml",
)
TRIVY_IGNORE_PATH = ROOT / ".trivyignore.yaml"
FRONTEND_SOURCE_PATH = ROOT / "web/antcode-frontend/src"
TRIVY_GATE_COUNT = 15
ACTION_COMMIT_SHA_LENGTH = 40


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_steps(paths: tuple[Path, ...]) -> list[dict]:
    return [step for path in paths for job in _workflow(path)["jobs"].values() for step in job.get("steps", [])]


def test_trivy_rsc_advisory_exception_is_narrow_and_expires() -> None:
    policy = yaml.safe_load(TRIVY_IGNORE_PATH.read_text(encoding="utf-8"))
    assert policy == {
        "vulnerabilities": [
            {
                "id": "GHSA-qwww-vcr4-c8h2",
                "paths": ["web/antcode-frontend/package-lock.json"],
                "expired_at": date(2026, 8, 31),
                "statement": (
                    "Not affected: AntCode is a React 19 SPA using react-router 8 browser APIs. "
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


def test_every_trivy_gate_loads_the_strict_exception_policy() -> None:
    steps = _all_steps((SECURITY_PATH, *RELEASE_PATHS))
    trivy_steps = [step for step in steps if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")]

    assert len(trivy_steps) == TRIVY_GATE_COUNT
    assert all(step["with"]["trivyignores"] == ".trivyignore.yaml" for step in trivy_steps)
    assert all(step["with"]["ignore-unfixed"] is False for step in trivy_steps)
    assert all(step["with"]["exit-code"] == "1" for step in trivy_steps)


def test_unsigned_candidate_and_release_e2e_have_no_signing_capability() -> None:
    orchestrator = _workflow(RELEASE_PATHS[0])["jobs"]
    candidate = _workflow(RELEASE_PATHS[1])["jobs"]["candidate"]
    release_e2e = _workflow(RELEASE_PATHS[2])["jobs"]["release-e2e"]
    candidate_source = RELEASE_PATHS[1].read_text(encoding="utf-8")
    e2e_source = RELEASE_PATHS[2].read_text(encoding="utf-8")

    assert orchestrator["release-e2e"]["needs"] == ["publish"]
    assert orchestrator["sign-services"]["needs"] == ["release-e2e"]
    assert orchestrator["finalize-release"]["needs"] == ["sign-services"]
    for job in (candidate, release_e2e):
        assert "id-token" not in job["permissions"]
        assert "attestations" not in job["permissions"]
    for source in (candidate_source, e2e_source):
        assert "cosign sign" not in source
        assert "attest-build-provenance" not in source


def test_release_e2e_never_builds_or_mutates_admin_database_directly() -> None:
    workflow = RELEASE_PATHS[2].read_text(encoding="utf-8")
    orchestrator = (ROOT / "scripts/release_e2e_orchestrator.py").read_text(encoding="utf-8")
    fixtures = (ROOT / "tests/e2e/conftest.py").read_text(encoding="utf-8")

    assert "docker compose build" not in workflow.lower()
    assert '"build"' not in orchestrator
    assert "docker-compose.prod.yml" in orchestrator
    assert "docker-compose.prod.worker.yml" in orchestrator
    assert "worker_credentials.json" in orchestrator
    assert "worker_registration_intent.json" in orchestrator
    for forbidden in ("Tortoise.init", "User.filter", ".set_password(", ".save("):
        assert forbidden not in fixtures


def test_release_artifacts_are_exact_and_fail_closed() -> None:
    candidate = RELEASE_PATHS[1].read_text(encoding="utf-8")
    prepare = (ROOT / "scripts/prepare_release_e2e.py").read_text(encoding="utf-8")
    finalizer = RELEASE_PATHS[3].read_text(encoding="utf-8")

    assert "release-digests/${SERVICE}.digest" in candidate
    assert "release-digests/${SERVICE}.platforms.json" in candidate
    assert "actual != expected" in prepare
    assert 'resolved["index"] != digest' in prepare
    assert 'resolved["amd64"] == resolved["arm64"]' in prepare
    assert "-eq 10" in finalizer
    assert 'keys == ["postgres", "redis", "reverse-proxy"]' in finalizer
    assert "release-deployment.json" in finalizer
    assert "release-deployment-descriptor" in finalizer
    assert "release_repository" in finalizer
    assert "release_digest" in finalizer
    assert "GITHUB_STEP_SUMMARY" in finalizer


def test_formal_release_tag_is_created_only_after_descriptor_upload() -> None:
    steps = _workflow(RELEASE_PATHS[3])["jobs"]["finalize"]["steps"]
    names = [step["name"] for step in steps]

    assert names[-2:] == ["Upload deployment descriptor", "Create single formal release tag"]


def test_all_external_workflow_actions_are_commit_pinned() -> None:
    workflow_paths = (CI_PATH, SECURITY_PATH, *RELEASE_PATHS)
    uses = [
        str(step["uses"])
        for step in _all_steps(workflow_paths)
        if "uses" in step and not str(step["uses"]).startswith("./")
    ]

    assert uses
    assert all(len(action.rsplit("@", maxsplit=1)[-1]) == ACTION_COMMIT_SHA_LENGTH for action in uses)
