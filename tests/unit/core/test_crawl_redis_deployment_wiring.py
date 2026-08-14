from pathlib import Path

from tests.unit.core.test_docker_compose_prod_contract import _compose, _script

DEPLOY_SCRIPT = Path("infra/docker/deploy-production.sh")
WEB_API_DOCKERFILE = Path("infra/docker/Dockerfile.web_api")
CRAWL_UPGRADE_DOC = Path("docs/crawl-redis-upgrade.md")
DEV_COMPOSE = Path("infra/docker/docker-compose.dev.yml")


def test_crawl_redis_upgrade_uses_candidate_image_and_minimum_secret() -> None:
    services = _compose()["services"]
    upgrade = services["crawl-redis-upgrade"]

    assert upgrade["image"] == services["migration"]["image"]
    assert upgrade["profiles"] == ["crawl-redis-upgrade"]
    assert upgrade["restart"] == "no"
    assert upgrade["read_only"] is True
    assert upgrade["cap_drop"] == ["ALL"]
    assert upgrade["security_opt"] == ["no-new-privileges:true"]
    assert upgrade["secrets"] == ["redis_url"]
    assert upgrade["environment"] == {
        "REDIS_URL_FILE": "/run/secrets/redis_url",
        "REDIS_NAMESPACE": "${REDIS_NAMESPACE:?REDIS_NAMESPACE is required}",
    }
    assert upgrade["networks"] == ["antcode-control"]


def test_development_upgrade_profile_uses_the_runtime_image_contract() -> None:
    upgrade = _compose(DEV_COMPOSE)["services"]["crawl-redis-upgrade"]

    assert upgrade["build"]["dockerfile"] == "infra/docker/Dockerfile.web_api"
    assert upgrade["profiles"] == ["crawl-redis-upgrade"]
    assert upgrade["read_only"] is True
    assert upgrade["command"] == ["python", "-m", "scripts.migrate_crawl_redis"]
    assert "${REDIS_NAMESPACE:?" in upgrade["environment"]["REDIS_NAMESPACE"]


def test_crawl_redis_upgrade_modules_are_in_runtime_image() -> None:
    dockerfile = _script(WEB_API_DOCKERFILE)

    for module in (
        "crawl_redis_upgrade_contract.py",
        "crawl_redis_upgrade_execution.py",
        "crawl_redis_upgrade_migration.py",
        "crawl_redis_upgrade_scan.py",
        "migrate_crawl_redis.py",
    ):
        assert f"scripts/{module}" in dockerfile


def test_deploy_script_keeps_existing_writers_stopped_until_reviewed_apply() -> None:
    deploy = _script(DEPLOY_SCRIPT)
    upgrade_run = "run --rm --no-deps crawl-redis-upgrade"

    assert 'stop --timeout "$STOP_TIMEOUT" web-api master gateway worker' in deploy
    assert deploy.index('stop --timeout "$STOP_TIMEOUT"') < deploy.index(upgrade_run)
    assert "existing-upgrade requires --confirm-writers-stopped" in deploy
    assert "--apply requires a prior dry-run and explicit --preflight-reviewed" in deploy
    assert "production deployment owns reserved argument" in deploy
    assert "fresh-deploy is read-only" in deploy
    assert "writers remain stopped" in deploy
    assert deploy.index(upgrade_run) < deploy.rindex("up -d --wait --wait-timeout")


def test_crawl_redis_docs_use_candidate_profile_for_production() -> None:
    deployment = _script(Path("infra/docker/README.md"))
    upgrade = _script(CRAWL_UPGRADE_DOC)

    for document in (deployment, upgrade):
        assert "deploy-production.sh .env.production fresh-deploy" in document
        assert "--confirm-writers-stopped" in document
        assert "--apply" in document
        assert "--preflight-reviewed" in document
        assert "writer" in document
    assert "crawl-redis-upgrade` profile" in upgrade
    docker_environment = _script(Path("infra/docker/.env.example"))
    assert "REDIS_NAMESPACE=antcode" in docker_environment
