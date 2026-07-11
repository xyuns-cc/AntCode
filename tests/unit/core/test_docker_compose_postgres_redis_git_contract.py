from pathlib import Path


def test_docker_compose_does_not_define_redis_password_fallback():
    compose_path = Path("infra/docker/docker-compose.dev.yml")

    compose = compose_path.read_text(encoding="utf-8")

    assert "REDIS_PASSWORD:-" not in compose
    assert "redis_password" not in compose
