from pathlib import Path

import yaml

COMPOSE_PATH = Path("infra/docker/docker-compose.dev.yml")
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
    assert "/usr/local/bin/docker-entrypoint.sh" in redis["command"][0]
    assert "su-exec" not in redis["command"][0]
    assert redis["healthcheck"]["test"] == [
        "CMD-SHELL",
        'REDISCLI_AUTH="$${REDIS_PASSWORD}" redis-cli ping',
    ]
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


def test_web_api_uses_a_non_root_compatible_named_data_volume():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert "web_data:/app/data" in compose["services"]["web-api"]["volumes"]
    assert compose["volumes"]["web_data"] == {"driver": "local"}


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
