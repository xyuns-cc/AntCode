from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_image_publication_is_called_after_all_ci_gates() -> None:
    workflow = _read(".github/workflows/ci.yml")

    assert "publish-images:" in workflow
    assert "uses: ./.github/workflows/docker-build.yml" in workflow
    for gate in (
        "backend-lint",
        "backend-test",
        "backend-security",
        "frontend-test",
        "frontend-security",
        "frontend-build",
    ):
        assert gate in workflow


def test_published_images_are_scanned_attested_and_signed() -> None:
    workflow = _read(".github/workflows/docker-build.yml")

    assert "Build scan candidate" in workflow
    # P1-CI-06: 先 push-by-digest（无 tag 不可拉取）→ 扫描该 digest →
    # 通过后才 imagetools 打正式标签，消除"带病标签可拉取时间窗"。
    assert "Scan pushed digest before tagging" in workflow
    assert "push-by-digest=true" in workflow
    assert "Tag verified digest" in workflow
    assert workflow.index("Scan pushed digest before tagging") < workflow.index("Tag verified digest")
    assert "@${{ steps.push.outputs.digest }}" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert "cosign sign --yes" in workflow
    assert "type=raw,value=latest" not in workflow


def test_publication_only_runs_via_protected_ci_and_uses_full_sha() -> None:
    publish_workflow = _read(".github/workflows/docker-build.yml")
    ci_workflow = _read(".github/workflows/ci.yml")

    assert "workflow_call:" in publish_workflow
    assert "workflow_dispatch:" not in publish_workflow
    assert "type=raw,value=sha-${{ github.sha }}" in publish_workflow
    assert "type=sha,prefix=sha-" not in publish_workflow
    assert "github.event_name == 'push'" in ci_workflow
    assert "github.ref == 'refs/heads/main'" in ci_workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in ci_workflow


def test_web_api_container_healthchecks_use_readiness() -> None:
    dockerfile = _read("infra/docker/Dockerfile.web_api")
    compose = _read("infra/docker/docker-compose.dev.yml")

    assert "/api/v1/health/ready" in dockerfile
    assert "/api/v1/health/ready" in compose
