import re
from pathlib import Path

from pathspec import GitIgnoreSpec

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "infra/docker/docker-compose.dev.yml"
ROOT_DOCKERIGNORE = ROOT / ".dockerignore"
TEST_DOCKERIGNORE = ROOT / "infra/docker/Dockerfile.test.dockerignore"
FRONTEND_DOCKERIGNORE = ROOT / "web/antcode-frontend/.dockerignore"
DOCKERIGNORE_PATHS = (ROOT_DOCKERIGNORE, TEST_DOCKERIGNORE, FRONTEND_DOCKERIGNORE)
DOCKERFILES = (
    ROOT / "infra/docker/Dockerfile.web_api",
    ROOT / "infra/docker/Dockerfile.master",
    ROOT / "infra/docker/Dockerfile.gateway",
    ROOT / "infra/docker/Dockerfile.worker",
    ROOT / "infra/docker/Dockerfile.test",
    ROOT / "web/antcode-frontend/Dockerfile",
)
CURL_PIPE_SHELL_PATTERN = re.compile(r"curl[^\n]*\|\s*(?:ba)?sh\b")
UV_VERSION = "0.8.17"
UV_IMAGE_DIGEST = "e4644cb5bd56fdc2c5ea3ee0525d9d21eed1603bccd6a21f887a938be7e85be1"
DOCKER_CONTEXT_SOURCE_FILES = (
    ROOT / "packages/antcode_core/src/antcode_core/application/services/logs/task_log_service.py",
    ROOT / "services/worker/src/antcode_worker/plugins/spider/data/reporter.py",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_compose_requires_non_empty_data_store_passwords():
    compose = _read(COMPOSE_PATH)

    for variable in ("POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        assert f"${{{variable}:?" in compose
        assert re.search(rf"(?<!\$)\$\{{{re.escape(variable)}\}}", compose) is None
        assert f"${{{variable}:-" not in compose


def test_compose_data_store_images_are_digest_pinned():
    compose = _read(COMPOSE_PATH)

    assert re.search(r"image: postgres:16-alpine@sha256:[0-9a-f]{64}", compose)
    assert re.search(r"image: redis:7\.4-alpine@sha256:[0-9a-f]{64}", compose)


def test_all_docker_base_images_are_digest_pinned():
    for path in DOCKERFILES:
        from_lines = [line for line in _read(path).splitlines() if line.startswith("FROM ")]
        assert from_lines
        assert all("@sha256:" in line for line in from_lines), path


def test_backend_images_copy_uv_from_fixed_image():
    for path in DOCKERFILES[:-1]:
        dockerfile = _read(path)
        assert f"ARG UV_VERSION={UV_VERSION}" in dockerfile
        assert f"ghcr.io/astral-sh/uv:${{UV_VERSION}}@sha256:{UV_IMAGE_DIGEST}" in dockerfile
        assert "COPY --from=uv-bin /uv /uvx /usr/local/bin/" in dockerfile
        assert not CURL_PIPE_SHELL_PATTERN.search(dockerfile)


def test_worker_mise_download_is_versioned_and_checksum_verified():
    dockerfile = _read(ROOT / "infra/docker/Dockerfile.worker")

    assert "ARG MISE_VERSION=2025.2.7" in dockerfile
    assert "MISE_SHA256_AMD64=92be0433432ecd579de77d3ba90fb61f1845d68be0602bc623cd0ff8a11892e3" in dockerfile
    assert "MISE_SHA256_ARM64=9d4e52b81490975d533e0d4535bb7307b5fa7f4605bf6636954c58084904dbe0" in dockerfile
    assert "amd64) mise_arch=x64" in dockerfile
    assert "arm64) mise_arch=arm64" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "https://mise.run" not in dockerfile


def test_worker_image_precreates_private_credential_directories():
    dockerfile = _read(ROOT / "infra/docker/Dockerfile.worker")

    assert "chmod 0700 /app/data/worker/secrets /app/data/worker/identity" in dockerfile


def test_worker_image_preinstalls_default_python_runtime():
    dockerfile = _read(ROOT / "infra/docker/Dockerfile.worker")

    assert "ARG DEFAULT_RUNTIME_PYTHON=3.12.11" in dockerfile
    assert 'uv python install "${DEFAULT_RUNTIME_PYTHON}"' in dockerfile
    assert 'uv python find "${DEFAULT_RUNTIME_PYTHON}"' in dockerfile


def test_web_api_image_contains_database_initializer():
    dockerfile = _read(ROOT / "infra/docker/Dockerfile.web_api")

    for path in (
        "scripts/__init__.py",
        "scripts/encrypt_sensitive_data.py",
        "scripts/init_db.py",
        "scripts/init_db_current_schema.py",
        "scripts/init_db_environment.py",
        "scripts/init_db_indexes.py",
        "scripts/init_db_legacy_schema.py",
        "scripts/init_db_schema_contracts.py",
        "scripts/init_db_schema_upgrades.py",
        "scripts/init_db_schema_validation.py",
        "scripts/migrate_worker_credentials.py",
        "scripts/migrate_worker_install_keys.py",
        "scripts/rotate_encryption_key.py",
        "scripts/rotate_worker_hmac_encryption_key.py",
        "migrations/models",
    ):
        assert path in dockerfile
    assert "/app/scripts/" in dockerfile


def test_web_api_runtime_contains_git_for_repository_scan():
    dockerfile = _read(ROOT / "infra/docker/Dockerfile.web_api")
    runtime = dockerfile.split(" AS runtime", maxsplit=1)[1]

    assert re.search(r"apt-get install[^&]*\bgit\b", runtime, re.DOTALL)


def test_application_images_use_strict_docker_secret_entrypoint():
    application_dockerfiles = DOCKERFILES[:4]

    for path in application_dockerfiles:
        dockerfile = _read(path)
        assert "entrypoint.load-secrets.sh /usr/local/bin/antcode-load-secrets" in dockerfile
        assert 'ENTRYPOINT ["/usr/local/bin/antcode-load-secrets"]' in dockerfile


def test_test_image_context_includes_tests_and_workspace_readmes():
    ignore_spec = GitIgnoreSpec.from_lines(_read(TEST_DOCKERIGNORE).splitlines())

    required_files = (
        "tests/e2e/conftest.py",
        "packages/antcode_core/README.md",
        "services/worker/README.md",
        "web/antcode-frontend/Dockerfile",
        "docs/database-setup.md",
        ".dockerignore",
    )
    assert all(not ignore_spec.match_file(path) for path in required_files)


def test_test_image_contains_cross_runtime_test_dependencies():
    dockerfile = _read(ROOT / "infra/docker/Dockerfile.test")

    # jq: infra/docker/verify-production-images.sh 与 deploy-production.sh 解析 compose
    # config 输出全靠它，缺它发布镜像校验在容器里必然失败。
    for package in ("jq", "nodejs", "postgresql-client"):
        assert package in dockerfile


def test_docker_context_excludes_static_analysis_caches():
    for dockerignore_path in (ROOT_DOCKERIGNORE, TEST_DOCKERIGNORE):
        dockerignore = _read(dockerignore_path)
        for cache_directory in (".mypy_cache/", ".ruff_cache/", ".hypothesis/"):
            assert cache_directory in dockerignore


def test_docker_contexts_exclude_environment_and_credential_files():
    excluded_paths = (
        ".env",
        ".env.production",
        ".env.production.local",
        ".envrc",
        "config.env",
        "config.env.backup",
        "nested/.env",
        "nested/.env.staging",
        "nested/config.env",
        "nested/private.key",
        "nested/credentials.json",
        "nested/cloud-credentials-prod.json",
        "nested/service-account-prod.json",
        "nested/.ssh/id_ed25519",
        "nested/.aws/credentials",
        "nested/.azure/accessTokens.json",
        "nested/.config/gcloud/application_default_credentials.json",
        "nested/.kube/config",
        "nested/.docker/config.json",
        "nested/.pgpass",
    )

    for dockerignore_path in DOCKERIGNORE_PATHS:
        ignore_spec = GitIgnoreSpec.from_lines(_read(dockerignore_path).splitlines())
        assert all(ignore_spec.match_file(path) for path in excluded_paths), dockerignore_path


def test_docker_contexts_retain_environment_examples():
    example_paths = (".env.example", "nested/.env.example")

    for dockerignore_path in DOCKERIGNORE_PATHS:
        ignore_spec = GitIgnoreSpec.from_lines(_read(dockerignore_path).splitlines())
        assert all(not ignore_spec.match_file(path) for path in example_paths), dockerignore_path


def test_docker_contexts_exclude_local_agent_configuration():
    agent_directories = (".agents", ".claude", ".codex", ".kiro", ".serena", ".cursor", ".continue", ".windsurf")
    excluded_paths = tuple(
        path
        for directory in agent_directories
        for path in (f"{directory}/settings.json", f"nested/{directory}/private.json")
    )

    for dockerignore_path in DOCKERIGNORE_PATHS:
        ignore_spec = GitIgnoreSpec.from_lines(_read(dockerignore_path).splitlines())
        assert all(ignore_spec.match_file(path) for path in excluded_paths), dockerignore_path


def test_git_and_docker_contexts_exclude_appledouble_metadata():
    metadata_paths = ("._source.py", "nested/deep/._source.py")
    ignore_paths = (ROOT / ".gitignore", *DOCKERIGNORE_PATHS)

    for ignore_path in ignore_paths:
        ignore_spec = GitIgnoreSpec.from_lines(_read(ignore_path).splitlines())
        assert all(ignore_spec.match_file(path) for path in metadata_paths), ignore_path


def test_docker_context_includes_nested_logs_and_data_source_files():
    for dockerignore_path in (ROOT_DOCKERIGNORE, TEST_DOCKERIGNORE):
        ignore_spec = GitIgnoreSpec.from_lines(_read(dockerignore_path).splitlines())
        for source_file in DOCKER_CONTEXT_SOURCE_FILES:
            relative_path = source_file.relative_to(ROOT).as_posix()
            assert source_file.is_file(), relative_path
            assert not ignore_spec.match_file(relative_path), relative_path


def test_runtime_directories_are_ignored_only_at_context_root():
    rules = set(_read(ROOT_DOCKERIGNORE).splitlines())

    for directory in ("data", "logs", "web", "docker", "docs"):
        assert f"/{directory}/" in rules
        assert f"{directory}/" not in rules


def test_nested_worker_runtime_credentials_are_excluded_from_git_and_build_context():
    nested_runtime_rule = "**/runtime_data/"

    assert nested_runtime_rule in _read(ROOT / ".gitignore").splitlines()
    for dockerignore_path in (ROOT_DOCKERIGNORE, TEST_DOCKERIGNORE):
        assert nested_runtime_rule in _read(dockerignore_path).splitlines()


def test_frontend_healthchecks_use_ipv4_loopback():
    dockerfile = _read(ROOT / "web/antcode-frontend/Dockerfile")
    compose = _read(COMPOSE_PATH)

    assert "http://127.0.0.1:8080/" in dockerfile
    assert "http://127.0.0.1:8080/" in compose
    assert "http://localhost:8080/" not in dockerfile
    assert "http://localhost:8080/" not in compose


def test_frontend_proxy_upload_limit_matches_web_api_default():
    dockerfile = _read(ROOT / "web/antcode-frontend/Dockerfile")
    core_config = _read(ROOT / "packages/antcode_core/src/antcode_core/common/config.py")

    assert "client_max_body_size 100m;" in dockerfile
    assert "MAX_FILE_SIZE: int = 100 * 1024 * 1024" in core_config


def test_gateway_health_dependency_is_declared_not_installed_ad_hoc():
    dockerfile = _read(ROOT / "infra/docker/Dockerfile.gateway")
    pyproject = _read(ROOT / "services/gateway/pyproject.toml")

    assert '"grpcio-health-checking>=1.60.0"' in pyproject
    assert "uv pip install" not in dockerfile
    assert "grpc-health-probe/releases/download" not in dockerfile


def test_web_api_container_healthchecks_use_readiness():
    assert "/api/v1/health/ready" in _read(ROOT / "infra/docker/Dockerfile.web_api")
    assert "/api/v1/health/ready" in _read(COMPOSE_PATH)
