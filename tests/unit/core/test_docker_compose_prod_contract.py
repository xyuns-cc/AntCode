import os
import subprocess
from pathlib import Path

import yaml

from tests.unit.core.compose_support import load_compose

PROD_COMPOSE = Path("infra/docker/docker-compose.prod.yml")
WORKER_COMPOSE = Path("infra/docker/docker-compose.prod.worker.yml")
LOCAL_BACKUP_COMPOSE = Path("infra/docker/docker-compose.prod.local-backup.yml")
ADMIN_BOOTSTRAP_COMPOSE = Path("infra/docker/docker-compose.prod.bootstrap-admin.yml")
WORKER_BOOTSTRAP_COMPOSE = Path("infra/docker/docker-compose.prod.bootstrap-worker.yml")
SECRET_ENTRYPOINT = Path("infra/docker/entrypoint.load-secrets.sh")
VERIFY_IMAGES = Path("infra/docker/verify-production-images.sh")
DEPLOY_SCRIPT = Path("infra/docker/deploy-production.sh")
BOOTSTRAP_WORKER = Path("infra/docker/bootstrap-worker.sh")
RESTORE_SCRIPT = Path("infra/docker/verify-backup-restore.sh")
RESOURCE_KEYS = {"cpus", "mem_limit", "pids_limit"}
DEVELOPMENT_COMPOSE_TARGET_COUNT = 3
CORE_SECRET_NAMES = {
    "database_url",
    "redis_url",
    "encryption_key",
    "encryption_key_salt",
    "encryption_keys_legacy",
}


def test_production_backend_services_require_exact_redis_namespace() -> None:
    compose = _compose()
    for name in ("migration", "web-api", "master", "gateway"):
        environment = compose["services"][name]["environment"]
        assert environment["REDIS_NAMESPACE"] == "${REDIS_NAMESPACE:?REDIS_NAMESPACE is required}"


def _compose(path: Path = PROD_COMPOSE) -> dict:
    return load_compose(path)


def _script(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_production_worker_is_backendless_singleton_and_secret_free() -> None:
    compose = _compose()
    worker = compose["services"]["worker"]
    environment = worker["environment"]

    assert worker["profiles"] == ["co-located-worker"]
    assert worker["container_name"].startswith("${ANTCODE_WORKER_CONTAINER_NAME:?")
    assert environment["WORKER_NAME"].startswith("${ANTCODE_WORKER_NAME:?")
    assert environment["WORKER_GATEWAY_BACKENDLESS"] == "true"
    assert environment["WORKER_API_ALLOW_INSECURE_INTERNAL"] == "false"
    assert environment["WORKER_GATEWAY_TLS"] == "true"
    assert "ANTCODE_WORKER_KEY" not in environment
    assert "env_file" not in worker
    assert {"DATABASE_URL", "REDIS_URL", "JWT_SECRET", "ENCRYPTION_KEY"}.isdisjoint(environment)
    assert compose["volumes"]["worker_data"]["name"].startswith("${ANTCODE_WORKER_DATA_VOLUME:?")


def test_worker_only_compose_requires_per_instance_identity_and_mtls() -> None:
    worker = _compose(WORKER_COMPOSE)["services"]["worker"]
    environment = worker["environment"]

    assert worker["container_name"].startswith("${ANTCODE_WORKER_CONTAINER_NAME:?")
    assert environment["WORKER_NAME"].startswith("${ANTCODE_WORKER_NAME:?")
    assert environment["WORKER_GATEWAY_HOST"].startswith("${ANTCODE_GATEWAY_HOST:?")
    assert environment["WORKER_GATEWAY_TLS"] == "true"
    assert any(str(volume).startswith("${ANTCODE_WORKER_TLS_DIR:?") for volume in worker["volumes"])
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    # security_opt 的完整集合由 test_worker_sandbox_security_contract.py 断言：
    # bwrap 的 user namespace 在 Docker 下还需要定制 seccomp profile +
    # apparmor/systempaths 放宽，缺了任务会 100% 失败。这里只锁"绝不能出现"的项。
    assert "no-new-privileges:true" in worker["security_opt"]
    assert "seccomp=unconfined" not in worker["security_opt"]
    assert "cap_add" not in worker


def test_worker_has_no_unconfined_or_privileged_compose_path() -> None:
    # 按解析后的配置判定，而不是在原文里搜字符串——否则连"解释我们为何不授予
    # CAP_SYS_ADMIN"的注释都会被判违规。
    #
    # 精确禁止真正危险的项：privileged、cap_add、seccomp=unconfined。
    # 不再笼统禁 "unconfined" 字样：bwrap 的非特权 user namespace 在 Docker 下
    # 必须放宽 apparmor 与 systempaths（已在真实宿主逐项二分验证，缺任一项每个
    # 任务都失败）。这两项只解除"容器阻止进程给自己建更严格沙箱"的限制，不授予
    # 任何宿主权限；seccomp 用的是定制 profile，默认动作仍是 SCMP_ACT_ERRNO。
    for compose_path in Path("infra/docker").glob("docker-compose*.yml"):
        services = (yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}).get("services") or {}
        for name, service in services.items():
            where = f"{compose_path.name}::{name}"
            assert service.get("privileged") is not True, where
            assert "cap_add" not in service, where
            security_opt = [str(opt) for opt in service.get("security_opt") or []]
            assert not any(opt.startswith(("seccomp=unconfined", "seccomp:unconfined")) for opt in security_opt), where


def test_control_and_execution_networks_are_separate() -> None:
    compose = _compose()
    services = compose["services"]

    assert compose["networks"]["antcode-control"]["internal"] is True
    assert services["postgres"]["networks"] == ["antcode-control"]
    assert services["redis"]["networks"] == ["antcode-control"]
    assert services["worker"]["networks"] == ["antcode-execution"]
    assert set(services["gateway"]["networks"]) == {"antcode-control", "antcode-execution"}
    assert set(services["frontend"]["networks"]) == {"antcode-control", "antcode-edge"}
    assert set(services["reverse-proxy"]["networks"]) == {"antcode-edge"}


def test_production_startup_orders_migration_before_consumers() -> None:
    services = _compose()["services"]

    assert services["migration"]["command"] == ["python", "-m", "scripts.init_db"]
    for name in ("web-api", "master", "gateway"):
        assert services[name]["depends_on"]["migration"] == {"condition": "service_completed_successfully"}
    assert services["frontend"]["depends_on"]["web-api"] == {"condition": "service_healthy"}


def test_core_credentials_are_docker_secrets_not_inline_environment() -> None:
    compose = _compose()
    services = compose["services"]
    secret_paths = compose["secrets"]

    assert CORE_SECRET_NAMES <= secret_paths.keys()
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD_FILE"] == "/run/secrets/postgres_password"
    assert "POSTGRES_PASSWORD" not in services["postgres"]["environment"]
    for service_name in ("migration", "web-api", "master", "gateway"):
        environment = services[service_name]["environment"]
        assert environment["DATABASE_URL_FILE"] == "/run/secrets/database_url"
        assert environment["REDIS_URL_FILE"] == "/run/secrets/redis_url"
        assert environment["ENCRYPTION_KEY_FILE"] == "/run/secrets/encryption_key"
        assert environment["ENCRYPTION_LEGACY_KDF_SALT"] == "${ENCRYPTION_LEGACY_KDF_SALT:-}"
        assert environment["ENCRYPTION_ALLOW_LEGACY_SHA256"] == "${ENCRYPTION_ALLOW_LEGACY_SHA256:-false}"
        assert {"DATABASE_URL", "REDIS_URL", "ENCRYPTION_KEY"}.isdisjoint(environment)
        assert CORE_SECRET_NAMES <= set(services[service_name]["secrets"])


def test_admin_and_worker_bootstrap_secrets_are_one_time_overrides() -> None:
    production = _compose()["services"]
    admin = _compose(ADMIN_BOOTSTRAP_COMPOSE)["services"]["migration"]
    worker = _compose(WORKER_BOOTSTRAP_COMPOSE)["services"]["worker"]

    assert "DEFAULT_ADMIN_PASSWORD" not in production["migration"]["environment"]
    assert "ANTCODE_WORKER_KEY" not in production["worker"]["environment"]
    assert admin["environment"] == {"DEFAULT_ADMIN_PASSWORD_FILE": "/run/secrets/default_admin_password"}
    assert worker["environment"] == {"ANTCODE_WORKER_KEY_FILE": "/run/secrets/worker_install_key"}
    assert "rm -f worker" in _script(BOOTSTRAP_WORKER)
    assert "up -d --no-deps --wait worker" in _script(BOOTSTRAP_WORKER)


def test_secret_entrypoint_loads_files_and_rejects_ambiguous_sources(tmp_path: Path) -> None:
    secret = tmp_path / "database_url"
    secret.write_text("postgresql://secret-source", encoding="utf-8")
    command = ["sh", str(SECRET_ENTRYPOINT), "sh", "-c", 'printf "%s" "$DATABASE_URL"']
    environment = {"PATH": os.environ["PATH"], "DATABASE_URL_FILE": str(secret)}

    loaded = subprocess.run(command, check=True, capture_output=True, text=True, env=environment)
    assert loaded.stdout == "postgresql://secret-source"
    ambiguous = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env={**environment, "DATABASE_URL": "inline"},
    )
    assert ambiguous.returncode != 0
    assert "cannot both be configured" in ambiguous.stderr


def test_all_production_services_have_resource_boundaries() -> None:
    service_sets = (_compose()["services"], _compose(LOCAL_BACKUP_COMPOSE)["services"])

    for services in service_sets:
        for service_name, service in services.items():
            assert RESOURCE_KEYS <= service.keys(), service_name
    redis_command = _compose()["services"]["redis"]["command"]
    assert redis_command[redis_command.index("--maxmemory-policy") + 1] == "noeviction"
    assert "--maxmemory" in redis_command


def test_runtime_healthchecks_terminate_pid_one_for_restart_policy() -> None:
    services = _compose()["services"]

    for service_name, service in services.items():
        if service_name in {"migration", "crawl-redis-upgrade"}:
            continue
        healthcheck = " ".join(service["healthcheck"]["test"])
        assert "kill -TERM 1" in healthcheck, service_name
        assert service["restart"] == "unless-stopped"


def test_local_backup_is_bounded_atomic_aged_and_recoverable() -> None:
    backup = _compose(LOCAL_BACKUP_COMPOSE)["services"]["backup-local"]
    script = backup["command"][-1]

    assert backup["profiles"] == ["local-backup"]
    assert "find /backup -type f -name '*.partial' -delete" in script
    assert "trap cleanup EXIT INT TERM HUP" in script
    assert 'timeout "$${BACKUP_TIMEOUT_SECONDS}" pg_dump' in script
    assert 'timeout "$${BACKUP_TIMEOUT_SECONDS}" pg_restore --list' in script
    assert "mv /backup/.last-success.partial /backup/.last-success" in script
    assert "BACKUP_MAX_AGE_SECONDS" in " ".join(backup["healthcheck"]["test"])
    restore = _script(RESTORE_SCRIPT)
    assert "sha256sum -c" in restore
    assert "--exit-on-error" in restore
    assert 'timeout "$RESTORE_TIMEOUT_SECONDS" pg_restore' in restore


def test_deployment_verifies_exact_signed_digests_before_pull_and_up() -> None:
    verify = _script(VERIFY_IMAGES)
    deploy = _script(DEPLOY_SCRIPT)

    assert "@sha256:[0-9a-f]{64}" in verify
    assert "cosign verify" in verify
    assert "--certificate-identity" in verify
    assert "--certificate-oidc-issuer" in verify
    assert "COSIGN_SERVICE_CERTIFICATE_IDENTITY" in verify
    assert "COSIGN_RELEASE_CERTIFICATE_IDENTITY" in verify
    assert deploy.index("verify-production-images.sh") < deploy.index('"${compose[@]}" pull')
    assert deploy.index('"${compose[@]}" pull') < deploy.index('"${compose[@]}" up')
    assert "--wait" in deploy


def test_production_images_are_site_supplied_exact_references() -> None:
    services = _compose()["services"]
    for service_name, service in services.items():
        image = service["image"]
        assert "_IMAGE_REPOSITORY:?" in image, service_name
        assert "@sha256:${" in image, service_name
        assert "_IMAGE_DIGEST:?" in image, service_name
        assert "build" not in service


def test_release_tags_must_belong_to_main_history() -> None:
    workflow = Path(".github/workflows/docker-build.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert '"refs/remotes/origin/main"' in workflow


def test_makefile_docker_targets_select_development_compose() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    expected = "docker compose -f infra/docker/docker-compose.dev.yml"

    assert makefile.count(expected) == DEVELOPMENT_COMPOSE_TARGET_COUNT
    assert "cd infra/docker && docker compose" not in makefile


def test_makefile_buildx_creates_only_local_oci_archives() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    buildx = makefile.split("docker-buildx:", maxsplit=1)[1].split("\n# ====", maxsplit=1)[0]

    assert "--platform linux/amd64,linux/arm64" in buildx
    assert "BUILDX_OUTPUT_DIR" in buildx
    assert '--output "type=oci,dest=' in buildx
    assert ".github/workflows/ci.yml" in buildx
    assert "--push" not in makefile
    assert "docker push" not in makefile
    assert "imagetools create" not in makefile
    for service in ("web-api", "master", "gateway", "worker", "frontend"):
        assert f'"{service}|' in buildx


def test_makefile_buildx_rejects_legacy_publication_parameters() -> None:
    legacy_variables = {"BUILDX_REGISTRY", "BUILDX_TAG"}
    base_environment = {key: value for key, value in os.environ.items() if key not in legacy_variables}

    for variable in legacy_variables:
        result = subprocess.run(
            ["make", "docker-buildx"],
            check=False,
            capture_output=True,
            text=True,
            env={**base_environment, variable: "test-only"},
        )
        assert result.returncode != 0
        assert "docker-buildx does not publish" in result.stdout
