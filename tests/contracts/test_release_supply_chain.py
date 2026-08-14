import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = ".github/workflows/docker-build.yml"
CANDIDATE = ".github/workflows/docker-build-candidate.yml"
RELEASE_E2E = ".github/workflows/docker-release-e2e.yml"
FINALIZER = ".github/workflows/docker-finalize-release.yml"
RELEASE_SERVICES = {"web-api", "master", "gateway", "worker", "frontend"}


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _workflow(relative_path: str) -> dict:
    return yaml.safe_load(_read(relative_path))


def _steps(relative_path: str, job: str) -> list[dict]:
    return _workflow(relative_path)["jobs"][job]["steps"]


def _step(steps: list[dict], name: str) -> dict:
    return next(step for step in steps if step.get("name") == name)


def _runtime_child_filter() -> str:
    run = _step(_steps(RELEASE_E2E, "release-e2e"), "Resolve locked runtime child digests")["run"]
    match = re.search(r'digest="\$\(jq -er --arg arch "\$arch" \'(.*?)\' <<<"\$manifest"\)"', run, re.DOTALL)
    assert match is not None
    return match.group(1)


def _resolve_child(program: str, index: dict, arch: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["jq", "-er", "--arg", "arch", arch, program],
        input=json.dumps(index),
        text=True,
        capture_output=True,
        check=False,
    )


def test_image_publication_is_called_after_all_ci_gates() -> None:
    workflow = _read(".github/workflows/ci.yml")

    assert "publish-images:" in workflow
    assert "uses: ./.github/workflows/docker-build.yml" in workflow
    for gate in (
        "backend-lint",
        "backend-test",
        "e2e",
        "backend-security",
        "frontend-test",
        "frontend-security",
        "frontend-build",
    ):
        assert gate in workflow


def test_release_dag_gates_signing_and_formal_tag_after_production_e2e() -> None:
    jobs = _workflow(ORCHESTRATOR)["jobs"]
    publish = jobs["publish"]

    assert set(publish["needs"]) == {"gitleaks", "hadolint", "trivy-fs"}
    gitleaks_names = [step["name"] for step in jobs["gitleaks"]["steps"]]
    assert gitleaks_names.index("Validate release tag format") < gitleaks_names.index(
        "Verify release tag belongs to main history"
    )
    assert publish["uses"] == "./.github/workflows/docker-build-candidate.yml"
    assert {item["service"] for item in publish["strategy"]["matrix"]["include"]} == RELEASE_SERVICES
    assert jobs["release-e2e"]["needs"] == ["publish"]
    assert jobs["release-e2e"]["uses"] == "./.github/workflows/docker-release-e2e.yml"
    assert jobs["sign-services"]["needs"] == ["release-e2e"]
    assert jobs["finalize-release"]["needs"] == ["sign-services"]
    assert jobs["finalize-release"]["uses"] == "./.github/workflows/docker-finalize-release.yml"


def test_candidate_publishes_only_unsigned_exact_multiarch_digest_descriptors() -> None:
    source = _read(CANDIDATE)
    steps = _steps(CANDIDATE, "candidate")
    names = [step["name"] for step in steps]
    push = _step(steps, "Build and push by digest (untagged)")
    resolve = _step(steps, "Resolve pushed platform digests")

    assert "push-by-digest=true" in push["with"]["outputs"]
    assert push["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert "imagetools inspect --raw" in resolve["run"]
    assert "expected exactly one linux/" in resolve["run"]
    assert "platform_digests[amd64]" in resolve["run"]
    for target in ("index digest", "amd64 child digest", "arm64 child digest"):
        scan = _step(steps, f"Scan pushed {target}")
        assert set(scan["with"]["scanners"].split(",")) == {"vuln", "secret"}
        assert scan["with"]["exit-code"] == "1"
        assert names.index(resolve["name"]) < names.index(scan["name"])
    assert "cosign sign" not in source
    assert "actions/attest-build-provenance@" not in source
    assert "imagetools create --tag" not in source
    assert "Upload unsigned digest descriptor" in names


def test_production_release_e2e_uses_exact_images_https_mtls_and_full_suite() -> None:
    source = _read(RELEASE_E2E)
    steps = _steps(RELEASE_E2E, "release-e2e")
    names = [step["name"] for step in steps]

    buildx = _step(steps, "Set up Docker Buildx")
    resolve = _step(steps, "Resolve locked runtime child digests")
    assert re.fullmatch(r"docker/setup-buildx-action@[0-9a-f]{40}", buildx["uses"])
    assert "imagetools inspect" in resolve["run"]
    assert "(.manifests | length) == 2" not in resolve["run"]
    assert 'select((.annotations["vnd.docker.reference.type"] // "") != "attestation-manifest")' in resolve["run"]
    assert 'if length == 1 then .[0] else error("expected exactly one linux/"' in resolve["run"]
    assert "^sha256:[0-9a-f]{64}$" in resolve["run"]
    assert "child_digests[amd64]" in resolve["run"]
    assert "child_digests[arm64]" in resolve["run"]
    assert '${child_digests[amd64]}" != "${child_digests[arm64]}' in resolve["run"]
    for runtime in ("PostgreSQL", "Redis", "reverse-proxy"):
        for target in ("digest", "amd64 child digest", "arm64 child digest"):
            scan = _step(steps, f"Scan locked {runtime} {target}")
            assert set(scan["with"]["scanners"].split(",")) == {"vuln", "secret"}
            assert names.index(resolve["name"]) < names.index(scan["name"])
            assert names.index(scan["name"]) < names.index("Prepare ephemeral production environment")
    prepare = _step(steps, "Prepare ephemeral production environment")["run"]
    start = _step(steps, "Start exact production control and Worker images")["run"]
    transport = _step(steps, "Verify HTTPS and Gateway mTLS")["run"]
    suite = _step(steps, "Run full Gateway production E2E including SSE")["run"]
    assert "-m scripts.prepare_release_e2e" in prepare
    assert "-m scripts.release_e2e_orchestrator" in start
    assert "-m scripts.verify_release_transport" in transport
    assert suite == "uv run --extra dev pytest tests/e2e -q"
    assert names.index("Verify HTTPS and Gateway mTLS") < names.index("Run full Gateway production E2E including SSE")
    assert "docker compose build" not in source
    assert "docker-compose.prod.ci-control.yml" in source
    assert "docker-compose.prod.ci-worker.yml" in source


def test_runtime_child_selector_allows_extra_descriptors_and_rejects_duplicate_platforms() -> None:
    amd64 = f"sha256:{'a' * 64}"
    arm64 = f"sha256:{'b' * 64}"
    base_manifests = [
        {"digest": f"sha256:{'c' * 64}", "platform": {"os": "linux", "architecture": "s390x"}},
        {
            "digest": f"sha256:{'d' * 64}",
            "platform": {"os": "linux", "architecture": "amd64"},
            "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
        },
        {"digest": amd64, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": arm64, "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
    ]
    program = _runtime_child_filter()

    for arch, expected in (("amd64", amd64), ("arm64", arm64)):
        result = _resolve_child(program, {"manifests": base_manifests}, arch)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    duplicate = {
        "digest": f"sha256:{'e' * 64}",
        "platform": {"os": "linux", "architecture": "amd64"},
    }
    rejected = _resolve_child(program, {"manifests": [*base_manifests, duplicate]}, "amd64")
    assert rejected.returncode != 0
    assert "expected exactly one linux/amd64 manifest" in rejected.stderr

    missing = _resolve_child(program, {"manifests": base_manifests[:-1]}, "arm64")
    assert missing.returncode != 0
    assert "expected exactly one linux/arm64 manifest" in missing.stderr


def test_service_signatures_and_attestations_consume_only_gated_descriptors() -> None:
    job = _workflow(ORCHESTRATOR)["jobs"]["sign-services"]
    steps = job["steps"]
    names = [step["name"] for step in steps]
    descriptor = _step(steps, "Read gated platform digests")["run"]
    sign = _step(steps, "Sign service index and platform digests")
    verify = _step(steps, "Verify service signatures and provenance attestations")

    assert job["needs"] == ["release-e2e"]
    assert "index_digest" in descriptor and "platforms.json" in descriptor
    assert "amd64" in descriptor and "arm64" in descriptor
    for name in ("Attest service index provenance", "Attest amd64 child provenance", "Attest arm64 child provenance"):
        assert _step(steps, name)["uses"].startswith("actions/attest-build-provenance@")
    assert "cosign sign --yes" in sign["run"]
    assert "cosign verify" in verify["run"]
    assert "gh attestation verify" in verify["run"]
    assert names.index("Read gated platform digests") < names.index("Attest service index provenance")
    assert names.index("Attest arm64 child provenance") < names.index(sign["name"]) < names.index(verify["name"])


def test_release_collection_binds_all_eight_images_and_is_tagged_last() -> None:
    steps = _steps(FINALIZER, "finalize")
    names = [step["name"] for step in steps]
    verify_services = _step(steps, "Verify signed service digest set")["run"]
    manifest = _step(steps, "Create complete release manifest")["run"]
    collection = _step(steps, "Build release collection by digest")
    verify_release = _step(steps, "Verify release collection signature and provenance attestation")
    formal_tag = _step(steps, "Create single formal release tag")["run"]
    ordered = (
        "Verify signed service digest set",
        "Build release collection by digest",
        "Scan release collection digest",
        "Attest release collection provenance",
        "Sign release collection digest",
        "Verify release collection signature and provenance attestation",
        "Create single formal release tag",
    )

    assert "-eq 10" in verify_services
    assert collection["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert "schema_version: 2" in manifest
    assert all(f"release-digests/{service}.digest" in manifest for service in RELEASE_SERVICES)
    assert all(runtime in manifest for runtime in ("postgres", "redis", '"reverse-proxy"'))
    assert list(map(names.index, ordered)) == sorted(map(names.index, ordered))
    assert "cosign verify" in verify_release["run"]
    assert "gh attestation verify" in verify_release["run"]
    assert "docker-finalize-release.yml@" in verify_release["env"]["CERTIFICATE_IDENTITY"]
    assert "existing_digest" in formal_tag
    assert "already points to a different digest" in formal_tag
    assert "tagged_digest" in formal_tag
    assert 'release_tag="sha-${GITHUB_SHA}"' in _read(FINALIZER)


def test_publication_only_runs_via_protected_ci_and_uses_full_sha() -> None:
    ci_workflow = _read(".github/workflows/ci.yml")
    all_release_sources = "\n".join(_read(path) for path in (ORCHESTRATOR, CANDIDATE, RELEASE_E2E, FINALIZER))

    assert "workflow_dispatch:" not in all_release_sources
    assert "github.event_name == 'push'" in ci_workflow
    assert "github.ref == 'refs/heads/main'" in ci_workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in ci_workflow
    assert all_release_sources.count("Create single formal release tag") == 1
    assert "type=raw,value=latest" not in all_release_sources


def test_web_api_container_healthchecks_use_readiness() -> None:
    assert "/api/v1/health/ready" in _read("infra/docker/Dockerfile.web_api")
    assert "/api/v1/health/ready" in _read("infra/docker/docker-compose.dev.yml")
