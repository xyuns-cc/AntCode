from pathlib import Path

import yaml

PROD_COMPOSE = Path("infra/docker/docker-compose.prod.yml")
LOCAL_BACKUP_COMPOSE = Path("infra/docker/docker-compose.prod.local-backup.yml")


def _compose(path: Path = PROD_COMPOSE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_production_worker_is_backendless_and_receives_no_backend_secrets() -> None:
    worker = _compose()["services"]["worker"]
    environment = worker["environment"]

    assert "env_file" not in worker
    assert environment["WORKER_GATEWAY_BACKENDLESS"] == "true"
    assert environment["WORKER_API_BASE_URL"].startswith("${ANTCODE_PUBLIC_API_BASE_URL:?")
    assert environment["WORKER_API_ALLOW_INSECURE_INTERNAL"] == "false"
    assert environment["WORKER_CA_CERT"] == "/etc/antcode/tls/ca.crt"
    assert environment["WORKER_CLIENT_CERT"] == "/etc/antcode/tls/client.crt"
    assert environment["WORKER_CLIENT_KEY"] == "/etc/antcode/tls/client.key"
    forbidden = {"DATABASE_URL", "REDIS_URL", "JWT_SECRET", "ENCRYPTION_KEY"}
    assert forbidden.isdisjoint(environment)


def test_production_gateway_uses_runtime_tls_names_and_http_readiness() -> None:
    gateway = _compose()["services"]["gateway"]
    environment = gateway["environment"]

    assert "env_file" not in gateway
    assert environment["GRPC_TLS_CERT_PATH"] == "/etc/antcode/tls/server.crt"
    assert environment["GRPC_TLS_KEY_PATH"] == "/etc/antcode/tls/server.key"
    assert environment["GRPC_TLS_CA_PATH"] == "/etc/antcode/tls/ca.crt"
    assert not any(key.startswith("GATEWAY_TLS_") for key in environment)
    assert gateway["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-fsS",
        "http://localhost:8100/health/ready",
    ]
    assert gateway["read_only"] is True
    assert "/tmp" in gateway["tmpfs"]


def test_production_startup_orders_migration_before_application_services() -> None:
    services = _compose()["services"]

    assert services["migration"]["command"] == ["python", "-m", "scripts.init_db"]
    for name in ("web-api", "master", "gateway"):
        dependency = services[name]["depends_on"]["migration"]
        assert dependency["condition"] == "service_completed_successfully"
    assert services["frontend"]["depends_on"]["web-api"]["condition"] == "service_healthy"
    assert services["reverse-proxy"]["depends_on"]["frontend"]["condition"] == "service_healthy"


def test_production_redis_has_persistent_acl_contract() -> None:
    redis = _compose()["services"]["redis"]
    command = redis["command"]

    assert command[command.index("--aclfile") + 1] == "/usr/local/etc/redis/users.acl"
    assert any(str(volume).endswith(":/usr/local/etc/redis") for volume in redis["volumes"])
    healthcheck = " ".join(redis["healthcheck"]["test"])
    assert "REDIS_HEALTHCHECK_USER" in healthcheck
    assert "REDISCLI_AUTH" in redis["environment"]


def test_migration_receives_all_strict_initialization_secrets() -> None:
    compose = _compose()
    migration = compose["services"]["migration"]
    environment = migration["environment"]

    assert "JWT_SECRET" not in environment
    assert environment["JWT_SECRET_FILE"] == "/run/secrets/jwt_secret"
    assert migration["secrets"] == ["jwt_secret"]
    assert compose["secrets"]["jwt_secret"]["file"].startswith("${ANTCODE_JWT_SECRET_FILE:?")
    assert "ENCRYPTION_KEY" in environment
    assert "ENCRYPTION_KEY_SALT" in environment
    assert "DEFAULT_ADMIN_PASSWORD" in environment


def test_web_api_receives_only_mounted_jwt_secret_file() -> None:
    web_api = _compose()["services"]["web-api"]

    assert web_api["environment"]["JWT_SECRET_FILE"] == "/run/secrets/jwt_secret"
    assert "JWT_SECRET" not in web_api["environment"]
    assert web_api["secrets"] == ["jwt_secret"]


def test_production_images_are_complete_site_supplied_references() -> None:
    services = _compose()["services"]
    expected = {
        "postgres": "ANTCODE_POSTGRES_IMAGE",
        "redis": "ANTCODE_REDIS_IMAGE",
        "migration": "ANTCODE_WEB_API_IMAGE",
        "web-api": "ANTCODE_WEB_API_IMAGE",
        "master": "ANTCODE_MASTER_IMAGE",
        "gateway": "ANTCODE_GATEWAY_IMAGE",
        "worker": "ANTCODE_WORKER_IMAGE",
        "frontend": "ANTCODE_FRONTEND_IMAGE",
        "reverse-proxy": "ANTCODE_REVERSE_PROXY_IMAGE",
    }
    for service_name, prefix in expected.items():
        image = services[service_name]["image"]
        assert image.startswith(f"${{{prefix}_REPOSITORY:?")
        assert f"@sha256:${{{prefix}_DIGEST:?" in image
        assert "build" not in services[service_name]


def test_read_only_frontend_has_writable_nginx_runtime_paths() -> None:
    frontend = _compose()["services"]["frontend"]

    assert frontend["read_only"] is True
    assert "/etc/nginx/conf.d" in frontend["tmpfs"]


def test_local_backup_is_atomic_verified_and_retained() -> None:
    backup = _compose(LOCAL_BACKUP_COMPOSE)["services"]["backup-local"]
    command = backup["command"]
    script = command[-1]

    assert backup["profiles"] == ["local-backup"]
    assert command[:2] == ["sh", "-ec"]
    assert 'pg_dump --dbname="$$DATABASE_URL"' in script
    assert 'pg_restore --list "$$partial"' in script
    assert 'sha256sum "$$partial"' in script
    assert 'mv "$$partial" "$$target"' in script
    assert 'mv "$$target.sha256.partial" "$$target.sha256"' in script
    assert "find /backup -type f" in script
    assert 'mtime "+$$BACKUP_RETENTION_DAYS"' in script
    assert "date -u +%Y%m%dT%H%M%SZ" in script
    assert "%%Y" not in script


def test_release_tags_must_belong_to_main_history() -> None:
    workflow = Path(".github/workflows/docker-build.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert '"refs/remotes/origin/main"' in workflow


def test_makefile_docker_targets_select_development_compose() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    expected = "docker compose -f infra/docker/docker-compose.dev.yml"
    expected_target_count = 3

    assert makefile.count(expected) == expected_target_count
    assert "cd infra/docker && docker compose" not in makefile


def test_makefile_buildx_pushes_every_production_service() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    buildx = makefile.split("docker-buildx:", maxsplit=1)[1].split("\n# ====", maxsplit=1)[0]

    assert "BUILDX_REGISTRY must be set" in buildx
    assert "BUILDX_TAG must be set" in buildx
    assert 'BUILDX_TAG}" != "latest"' in buildx
    assert "--push" in buildx
    for service in ("web-api", "master", "gateway", "worker", "frontend"):
        assert f'"{service}|' in buildx
