import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERIFY = ROOT / "infra/docker/verify-production-images.sh"
DEPLOY = ROOT / "infra/docker/deploy-production.sh"
BOOTSTRAP_ADMIN = ROOT / "infra/docker/bootstrap-admin.sh"
BOOTSTRAP_WORKER = ROOT / "infra/docker/bootstrap-worker.sh"
DEPLOY_WORKER = ROOT / "infra/docker/deploy-worker.sh"
RELEASE_COMPOSE = ROOT / "infra/docker/docker-compose.release.yml"
RUNTIME_LOCK = ROOT / "infra/docker/release-runtime-images.json"
SHA256_LENGTH = 64
REVISION_LENGTH = 40
APPLICATION_SERVICE_COUNT = 5
RUNTIME_SERVICE_COUNT = 3
COSIGN_VERIFICATION_COUNT = 6
SERVICE_IDENTITY = "https://github.com/example/antcode/.github/workflows/docker-build.yml@refs/heads/main"
RELEASE_IDENTITY = "https://github.com/example/antcode/.github/workflows/docker-finalize-release.yml@refs/heads/main"
REVISION = "b" * REVISION_LENGTH
RELEASE_IMAGE = f"ghcr.io/example/antcode-release@sha256:{'a' * SHA256_LENGTH}"
IMAGE_PREFIXES = {
    "web-api": "ghcr.io/example/antcode-web-api",
    "master": "ghcr.io/example/antcode-master",
    "gateway": "ghcr.io/example/antcode-gateway",
    "worker": "ghcr.io/example/antcode-worker",
    "frontend": "ghcr.io/example/antcode-frontend",
    "postgres": "postgres:16-alpine",
    "redis": "redis:7.4-alpine",
    "reverse-proxy": "nginxinc/nginx-unprivileged:1.27-alpine",
}
IMAGES = {
    name: f"{repository}@sha256:{character * SHA256_LENGTH}"
    for (name, repository), character in zip(IMAGE_PREFIXES.items(), "cdef0123", strict=True)
}

FAKE_DOCKER = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
if [[ "$1" == "compose" && "$*" == *"docker-compose.release.yml"* ]]; then
    jq -n --arg image "$RELEASE_IMAGE" --arg revision "$EXPECTED_REVISION" \
        '{services: {"release-collection": {image: $image, labels: {"io.antcode.release.revision": $revision}}}}'
elif [[ "$1" == "compose" && "$*" == *"config --images"* ]]; then
    case "${*: -1}" in
        web-api) printf '%s\n' "$WEB_API_IMAGE" ;;
        master) printf '%s\n' "$MASTER_IMAGE" ;;
        gateway) printf '%s\n' "$GATEWAY_IMAGE" ;;
        worker) printf '%s\n' "$WORKER_IMAGE" ;;
        frontend) printf '%s\n' "$FRONTEND_IMAGE" ;;
        postgres) printf '%s\n' "$POSTGRES_IMAGE" ;;
        redis) printf '%s\n' "$REDIS_IMAGE" ;;
        reverse-proxy) printf '%s\n' "$REVERSE_PROXY_IMAGE" ;;
        *) exit 91 ;;
    esac
elif [[ "$1" == "image" && "$2" == "inspect" ]]; then
    image="${*: -1}"
    if [[ -n "${MISMATCH_IMAGE:-}" && "$image" == "$MISMATCH_IMAGE" ]]; then
        printf '%s\n' "${MISMATCH_REVISION}"
    else
        printf '%s\n' "$EXPECTED_REVISION"
    fi
elif [[ "$1" == "create" ]]; then
    printf '%s\n' fake-release-container
elif [[ "$1" == "cp" ]]; then
    cp "$FAKE_MANIFEST" "$3"
fi
"""

FAKE_COSIGN = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_COSIGN_LOG"
"""


def _manifest() -> dict:
    return {"schema_version": 2, "revision": REVISION, "images": dict(IMAGES)}


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _test_environment(tmp_path: Path, manifest: dict) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "docker", FAKE_DOCKER)
    _write_executable(fake_bin / "cosign", FAKE_COSIGN)
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COSIGN_SERVICE_CERTIFICATE_IDENTITY": SERVICE_IDENTITY,
        "COSIGN_RELEASE_CERTIFICATE_IDENTITY": RELEASE_IDENTITY,
        "EXPECTED_REVISION": REVISION,
        "RELEASE_IMAGE": RELEASE_IMAGE,
        "FAKE_MANIFEST": str(manifest_path),
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
        "FAKE_COSIGN_LOG": str(tmp_path / "cosign.log"),
        "MISMATCH_IMAGE": "",
        "MISMATCH_REVISION": "f" * REVISION_LENGTH,
    }
    for service, image in IMAGES.items():
        environment[f"{service.replace('-', '_').upper()}_IMAGE"] = image
    env_file = tmp_path / "production.env"
    env_file.write_text("DEPLOYMENT_TEST=1\n", encoding="utf-8")
    return environment, env_file


def _run_verifier(
    tmp_path: Path,
    manifest: dict,
    *,
    mode: str | None = None,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment, env_file = _test_environment(tmp_path, manifest)
    environment.update(environment_overrides or {})
    command = [str(VERIFY), str(env_file)]
    if mode is not None:
        command.append(mode)
    return subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30, check=False)


def test_complete_release_collection_is_verified_against_all_eight_images(tmp_path: Path) -> None:
    result = _run_verifier(tmp_path, _manifest())

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("verified release member:") == APPLICATION_SERVICE_COUNT
    assert result.stdout.count("verified release runtime member:") == RUNTIME_SERVICE_COUNT
    assert f"verified atomic release collection: {REVISION}" in result.stdout
    cosign_calls = (tmp_path / "cosign.log").read_text(encoding="utf-8").splitlines()
    assert len(cosign_calls) == COSIGN_VERIFICATION_COUNT
    assert RELEASE_IDENTITY in cosign_calls[0]
    assert all(SERVICE_IDENTITY in call for call in cosign_calls[1:])


def test_cross_release_service_digest_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["images"]["gateway"] = f"ghcr.io/example/antcode-gateway@sha256:{'0' * SHA256_LENGTH}"

    result = _run_verifier(tmp_path, manifest)

    assert result.returncode != 0
    assert "gateway image does not belong to the selected release collection" in result.stderr


def test_release_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["revision"] = "0" * REVISION_LENGTH

    result = _run_verifier(tmp_path, manifest)

    assert result.returncode != 0
    assert "release manifest contract validation failed" in result.stderr


def test_manifest_with_unexpected_image_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["images"]["debug"] = f"example/debug@sha256:{'0' * SHA256_LENGTH}"

    result = _run_verifier(tmp_path, manifest)

    assert result.returncode != 0
    assert "release manifest contract validation failed" in result.stderr


def test_service_oci_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    result = _run_verifier(
        tmp_path,
        _manifest(),
        environment_overrides={"MISMATCH_IMAGE": IMAGES["master"]},
    )

    assert result.returncode != 0
    assert "master image revision does not match the release collection" in result.stderr


def test_worker_only_verification_does_not_require_runtime_compose_images(tmp_path: Path) -> None:
    result = _run_verifier(tmp_path, _manifest(), mode="--worker-only")

    assert result.returncode == 0, result.stderr
    docker_log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "config --images worker" in docker_log
    assert "config --images postgres" not in docker_log


def test_deploy_reuses_one_frozen_environment_snapshot(tmp_path: Path) -> None:
    environment, env_file = _test_environment(tmp_path, _manifest())

    result = subprocess.run(
        [str(DEPLOY), str(env_file), "fresh-deploy"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    docker_log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    env_files = {
        line.split("--env-file ", maxsplit=1)[1].split(maxsplit=1)[0]
        for line in docker_log.splitlines()
        if "--env-file " in line
    }
    assert len(env_files) == 1
    assert env_files != {str(env_file)}


def test_every_production_entrypoint_verifies_the_frozen_environment_before_pull() -> None:
    for path in (DEPLOY, BOOTSTRAP_ADMIN, BOOTSTRAP_WORKER, DEPLOY_WORKER):
        source = path.read_text(encoding="utf-8")
        assert 'ENV_FILE="$(mktemp)"' in source, path.name
        assert 'cp -- "$SOURCE_ENV_FILE" "$ENV_FILE"' in source, path.name
        verify_index = source.index('verify-production-images.sh" "$ENV_FILE"')
        pull_or_start = min(index for marker in ('" pull', '" up -d') if (index := source.find(marker)) >= 0)
        assert verify_index < pull_or_start, path.name


def test_release_metadata_compose_requires_exact_collection_and_revision() -> None:
    source = RELEASE_COMPOSE.read_text(encoding="utf-8")

    assert "ANTCODE_RELEASE_IMAGE_REPOSITORY:?" in source
    assert "@sha256:${ANTCODE_RELEASE_IMAGE_DIGEST:?" in source
    assert "io.antcode.release.revision: ${ANTCODE_RELEASE_REVISION:?" in source
    assert 'profiles: ["release-metadata"]' in source


def test_runtime_lock_contains_only_the_three_scanned_exact_images() -> None:
    lock = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))

    assert lock["schema_version"] == 1
    assert set(lock["images"]) == {"postgres", "redis", "reverse-proxy"}
    for service, image in lock["images"].items():
        repository, digest = image.rsplit("@sha256:", maxsplit=1)
        assert repository == IMAGE_PREFIXES[service]
        assert len(digest) == SHA256_LENGTH
        int(digest, 16)
