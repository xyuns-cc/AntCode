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
    assert "Scan published digest" in workflow
    assert "@${{ steps.push.outputs.digest }}" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert "cosign sign --yes" in workflow
    assert "type=raw,value=latest" not in workflow


def test_web_api_container_healthchecks_use_readiness() -> None:
    dockerfile = _read("infra/docker/Dockerfile.web_api")
    compose = _read("infra/docker/docker-compose.dev.yml")

    assert "/api/v1/health/ready" in dockerfile
    assert "/api/v1/health/ready" in compose
