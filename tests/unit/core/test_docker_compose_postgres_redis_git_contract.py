import json
import shutil
import subprocess
from pathlib import Path

import yaml

COMPOSE_PATH = Path("infra/docker/docker-compose.dev.yml")
REMOTE_COMPOSE_PATH = Path("infra/docker/docker-compose.remote.yml")
REMOTE_GATEWAY_COMPOSE_PATH = Path("infra/docker/docker-compose.remote.gateway.yml")
ROOT_ENV_EXAMPLE_PATH = Path(".env.example")
DOCKER_ENV_EXAMPLE_PATH = Path("infra/docker/.env.example")
DOCKER_README_PATH = Path("infra/docker/README.md")


def test_docker_compose_does_not_define_redis_password_fallback():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "REDIS_PASSWORD:-" not in compose
    assert "redis_password" not in compose


def test_dev_redis_persists_acl_and_control_plane_enables_it():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]
    redis = services["redis"]

    assert "--aclfile" in redis["command"][0]
    assert "su-exec redis redis-server" in redis["command"][0]
    assert "redis_acl:/etc/redis-acl" in redis["volumes"]
    for service_name in ("web-api", "master", "gateway"):
        assert "REDIS_ACL_ENABLED=true" in services[service_name]["environment"]


def test_direct_worker_waits_for_web_api_and_uses_service_url():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]

    assert "WORKER_API_BASE_URL=http://web-api:8000" in worker["environment"]
    assert worker["depends_on"]["web-api"] == {"condition": "service_healthy"}
    assert "env_file" not in worker
    names = {entry.split("=", maxsplit=1)[0] for entry in worker["environment"]}
    assert "DATABASE_URL" not in names
    assert "REDIS_URL" not in names
    assert "WORKER_REDIS_URL=redis://redis:6379/0" in worker["environment"]
    assert "REDIS_ACL_ENABLED=true" in worker["environment"]
    assert (
        "ANTCODE_WORKER_KEY=${ANTCODE_WORKER_KEY:?generate a one-time Worker install key first}"
        in worker["environment"]
    )


def test_worker_grants_only_required_nested_sandbox_options():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]

    assert worker.get("privileged") is not True
    assert worker.get("user") not in {"0", 0, "root"}
    assert worker["cap_add"] == ["SYS_ADMIN"]
    assert set(worker["security_opt"]) == {
        "seccomp=unconfined",
        "apparmor=unconfined",
        "systempaths=unconfined",
    }


def test_remote_worker_grants_required_nested_sandbox_options():
    compose = yaml.safe_load(REMOTE_COMPOSE_PATH.read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]

    assert worker.get("privileged") is not True
    assert worker.get("user") not in {"0", 0, "root"}
    assert worker["cap_add"] == ["SYS_ADMIN"]
    assert set(worker["security_opt"]) == {
        "seccomp=unconfined",
        "apparmor=unconfined",
        "systempaths=unconfined",
    }


def test_remote_backend_uses_shared_named_volume():
    compose = yaml.safe_load(REMOTE_COMPOSE_PATH.read_text(encoding="utf-8"))

    assert compose["services"]["web-api"]["volumes"] == ["backend_data:/app/data"]
    assert compose["services"]["master"]["volumes"] == ["backend_data:/app/data"]
    assert compose["volumes"]["backend_data"] == {"driver": "local"}


def test_remote_worker_is_backendless_by_default():
    compose = yaml.safe_load(REMOTE_COMPOSE_PATH.read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]
    environment = worker["environment"]

    assert "env_file" not in worker
    assert environment["ANTCODE_WORKER_KEY"] == "${ANTCODE_WORKER_KEY:-}"
    assert environment["WORKER_TRANSPORT_MODE"] == "gateway"
    assert environment["WORKER_GATEWAY_BACKENDLESS"] == "true"
    assert environment["WORKER_GATEWAY_HOST"] == "gateway"
    assert environment["WORKER_GATEWAY_PORT"] == "50051"
    assert environment["WORKER_GATEWAY_TLS"] == "false"
    for forbidden in ("DATABASE_URL", "REDIS_URL", "WORKER_REDIS_URL", "REDIS_ACL_ENABLED"):
        assert forbidden not in environment


def test_remote_private_git_allowance_is_scoped_to_master():
    compose = yaml.safe_load(REMOTE_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["master"]["environment"]["ALLOW_PRIVATE_NODES"] == "true"
    for service_name in ("web-api", "gateway", "worker"):
        assert "ALLOW_PRIVATE_NODES" not in services[service_name]["environment"]


def test_remote_gateway_override_removes_direct_transport_url():
    compose = yaml.safe_load(REMOTE_GATEWAY_COMPOSE_PATH.read_text(encoding="utf-8"))
    environment = compose["services"]["worker"]["environment"]

    assert environment == {
        "DATABASE_URL": "",
        "REDIS_URL": "",
        "WORKER_ID": "",
        "WORKER_TRANSPORT_MODE": "gateway",
        "WORKER_GATEWAY_BACKENDLESS": "true",
        "WORKER_REDIS_URL": "",
        "ANTCODE_WORKER_KEY": "${ANTCODE_WORKER_KEY:-}",
        "WORKER_GATEWAY_HOST": "gateway",
        "WORKER_GATEWAY_PORT": "50051",
        "WORKER_GATEWAY_TLS": "false",
    }


def test_remote_gateway_final_worker_environment_is_backendless(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None, "docker CLI is required for compose contract tests"
    docker_dir = tmp_path / "infra" / "docker"
    docker_dir.mkdir(parents=True)
    base = docker_dir / REMOTE_COMPOSE_PATH.name
    override = docker_dir / REMOTE_GATEWAY_COMPOSE_PATH.name
    base.write_text(REMOTE_COMPOSE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    override.write_text(REMOTE_GATEWAY_COMPOSE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://test:secret@postgres:5432/antcode\n"
        "REDIS_URL=redis://:secret@redis:6379/0\n"
        "WORKER_REDIS_URL=redis://:worker-secret@redis:6379/0\n"
        "ANTCODE_WORKER_KEY=install-key-sentinel\n"
        "JWT_SECRET=server-only\n"
        "ENCRYPTION_KEY=server-only\n"
        "DEFAULT_ADMIN_PASSWORD=server-only\n",
        encoding="utf-8",
    )
    command = [
        docker,
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(base),
        "-f",
        str(override),
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
    environment = json.loads(result.stdout)["services"]["worker"]["environment"]

    assert environment["WORKER_TRANSPORT_MODE"] == "gateway"
    assert environment["WORKER_GATEWAY_BACKENDLESS"] == "true"
    assert environment["ANTCODE_WORKER_KEY"] == "install-key-sentinel"
    assert all(environment[name] == "" for name in ("DATABASE_URL", "REDIS_URL", "WORKER_REDIS_URL"))
    assert not {"JWT_SECRET", "ENCRYPTION_KEY", "DEFAULT_ADMIN_PASSWORD"} & environment.keys()


def test_remote_web_api_uses_published_frontend_port() -> None:
    compose = yaml.safe_load(REMOTE_COMPOSE_PATH.read_text(encoding="utf-8"))

    assert compose["services"]["web-api"]["environment"]["FRONTEND_PORT"] == "${FRONTEND_PORT:-3000}"


def test_trusted_proxy_examples_are_explicit_and_fail_closed() -> None:
    examples = (
        ROOT_ENV_EXAMPLE_PATH.read_text(encoding="utf-8"),
        DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8"),
    )

    for example in examples:
        assert example.splitlines().count("ANTCODE_TRUSTED_PROXIES=") == 1
        assert "ANTCODE_TRUSTED_PROXIES=0.0.0.0/0" not in example
        assert "ANTCODE_TRUSTED_PROXIES=::/0" not in example

    documentation = DOCKER_README_PATH.read_text(encoding="utf-8")
    assert "与 Web API 直接建立" in documentation
    assert "不要填写 `0.0.0.0/0`、`::/0`" in documentation
